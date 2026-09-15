import json
import unittest
from pathlib import Path

from newsdesk.fetch import parse_html


ROOT = Path(__file__).resolve().parents[1]


class FrontendExperienceTests(unittest.TestCase):
    def test_home_has_welcome_tour_and_no_report_tab(self):
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        self.assertNotIn('href="/report.html"', html)
        self.assertIn('id="btn-about"', html)
        self.assertIn("为什么有 NEWSDESK", script)
        self.assertIn("第一次使用，只记住三点", script)
        self.assertIn("阅读原始新闻", script)
        self.assertIn("最多 900 字", script)
        # 上手按钮的 tooltip 承诺了快捷键，弹窗里就必须真的有一份。
        self.assertIn('title="三步上手与快捷键"', html)
        self.assertIn("键盘快捷键", script)
        self.assertIn("<kbd>Ctrl K</kbd>", script)

    def test_body_preview_prefers_extracted_article(self):
        script = (ROOT / "web" / "app.js").read_text()
        # 正文优先级：中文模式先用 body_zh，再回退原文 body，最后才是入库摘要。
        self.assertIn("bodyText||lead?.summary", script.replace(" ", ""))
        self.assertIn("lead.body_zh:lead?.body", script.replace(" ", ""))
        self.assertIn("展示${fromBody?\"原文正文\":\"入库摘要\"}的前", script)

    def test_admin_console_is_a_separate_page_not_mixed_into_reader_view(self):
        """管理后台是独立 URL 的独立页面：读者页上一个管理控件都不留。

        以前是同一个页面用 .admin-only 藏起来、令牌解锁后再显示 —— 两类使用者
        混在一个界面里，读者顶栏挂着刷新/信源/监控这些跟他无关的按钮。现在彻底拆开。
        """
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        admin_html = (ROOT / "web" / "admin.html").read_text()
        admin_js = (ROOT / "web" / "admin.js").read_text()
        server = (ROOT / "newsdesk" / "server.py").read_text()

        # 读者页：既没有管理按钮，也没有解锁入口，也不再有 admin 模式代码
        for gone in ('id="btn-sources"', 'id="btn-quality"', 'id="btn-alerts"',
                     'id="btn-refresh"', 'id="btn-lock"', 'id="btn-research"',
                     'id="btn-changelog"', "admin-only"):
            self.assertNotIn(gone, html, f"读者页不该还有 {gone}")
        for gone in ("function applyAdmin", "function toggleAdmin", "function isAdmin",
                     "showSources", "showAlerts", "/api/admin/verify"):
            self.assertNotIn(gone, script, f"app.js 不该还有 {gone}")
        # 读者页也不再向读者索要管理令牌（他们没有令牌，弹窗只会让人以为坏了）
        self.assertNotIn("newsdeskWriteToken", script)

        # 管理页：独立入口 + 令牌闸门 + 各治理面板
        self.assertIn('src="/static/admin.js"', admin_html)
        self.assertIn('id="ad-token"', admin_html)
        self.assertIn("/api/admin/verify", admin_js)
        self.assertIn("sessionStorage", admin_js)      # 令牌不落磁盘
        self.assertNotIn("localStorage", admin_js)
        for sec in ("overview", "sources", "quality", "alerts", "watchlist",
                    "voice", "translate", "ops"):
            self.assertIn(f'data-sec="{sec}"', admin_html, sec)
        # 服务端把 /admin、/admin.html、/console 都指到这张静态壳
        self.assertIn('"/admin", "/admin.html", "/console"', server)
        self.assertIn('self._static("admin.html")', server)
        # 拆开的是界面，不是安全边界：写操作照旧由服务端令牌拦
        self.assertIn("_write_authenticated", server)

    def test_senior_mode_is_public_themed_tts_and_plain(self):
        """老人版：顶部常驻入口对所有人可见(非 admin-only)、浅色高对比主题、
        浏览器自带语音朗读、术语换大白话、首访轻问一次。"""
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        css = (ROOT / "web" / "style.css").read_text()
        # 入口是公共的，不能被 admin-only 隐藏
        i = html.find('id="btn-senior"')
        self.assertNotEqual(i, -1, "缺老人版按钮")
        line = html[html.rfind("<", 0, i):html.find(">", i) + 1]
        self.assertNotIn("admin-only", line, "老人版入口必须对所有人可见")
        # 开关 / 主题 / 朗读 / 大白话 的实现都在
        for token in ("function isSenior", "function applySenior", "function toggleSenior",
                      "function askSenior", "nd_senior", "srLabels", "function credLabel",
                      "speechSynthesis", "SpeechSynthesisUtterance", "function speak"):
            self.assertIn(token, script, token)
        self.assertIn("好多家媒体在报", script)        # 术语 → 大白话
        self.assertIn("家媒体在说", script)
        # 启动时套主题 + 探测语音；按钮接上 toggleSenior
        self.assertIn("applySenior()", script)
        self.assertIn("ttsInit()", script)
        self.assertIn("_bs.onclick=toggleSenior", script)
        # 浅色高对比主题 + 无中文语音时隐藏朗读按钮(优雅降级)
        for token in ("body.senior", "body.no-tts", ".sr-read", ".sr-btn"):
            self.assertIn(token, css, token)

    def test_senior_mode_is_a_reading_layout_not_a_recolor(self):
        """老人版改版：第一版只把暗色调成浅色、字号调大，版式还是三栏密排的交易终端 ——
        『太丑』说的是版式，不是配色。这里锁住改版后真正解决问题的几件事。"""
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        css = (ROOT / "web" / "style.css").read_text()
        senior = css[css.find("body.senior"):]

        # ① 单栏阅读流：侧栏/KPI/行情条这些终端部件在老人版里必须消失
        self.assertIn("body.senior .main{", senior.replace("\n", ""))
        self.assertRegex(senior, r"body\.senior[^{]*\.side[^{]*\{[^}]*display:none")
        for hide in (".kpis", ".market-strip"):
            self.assertIn(hide, senior, f"老人版应隐藏 {hide}")
        # ② 卡片式条目：大圆形可信度徽章 + 不截断的衬线标题
        self.assertIn("grid-template-areas", senior)
        self.assertIn("--sr-serif", senior)
        self.assertIn("-webkit-line-clamp:unset", senior.replace(" ", ""))
        # ③ 字号三档：状态挂在 body 的 data 属性上，CSS 只管呈现
        self.assertIn('body.senior[data-srsize="2"]', senior)
        self.assertIn('body.senior[data-srsize="3"]', senior)
        for token in ("function bumpSrSize", "function applySrSize",
                      "dataset.srsize", "SR_SIZES"):
            self.assertIn(token, script, token)
        # ④ 老人版专属工具条：顶栏那排小按钮不好点，控制项收在这里
        self.assertIn('id="sr-font-up"', html)
        self.assertIn('id="sr-font-dn"', html)
        self.assertIn('id="sr-exit"', html)      # 一键回标准版，不用去找顶栏按钮
        self.assertIn(".sr-bar", senior)
        self.assertIn("$(\"#sr-exit\").onclick=toggleSenior", script)

    def test_senior_mode_reads_the_whole_list_not_one_item_at_a_time(self):
        """连读：老人不该为了听新闻一条一条去点。锁住三件事 ——
        ① 队列级的开始/暂停-继续/上下条/停止都在；② 队列存 id 不存下标（列表重绘不会读串）；
        ③ 停止不能被 cancel() 触发的 onend 误当成『念完了』而变成快进。"""
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        css = (ROOT / "web" / "style.css").read_text()

        # ① 入口与四个控制键：底部整条播报条，不是右下角悬浮圆钮
        #    （圆钮放不下四个动作，也显示不了「念到第几条」）
        self.assertIn('id="sr-play-all"', html)
        for bid in ("sr-player", "sr-player-pos", "sr-player-title",
                    "sr-player-prev", "sr-player-toggle", "sr-player-next", "sr-player-stop"):
            self.assertIn(f'id="{bid}"', html, bid)
        for token in ("function playStart", "function playStop", "function playToggle",
                      "function playAt", "function playPaint", "function ttsPause",
                      "function ttsResume"):
            self.assertIn(token, script, token)
        self.assertIn('$("#sr-play-all").onclick', script)
        self.assertIn('$("#sr-player-toggle").onclick=playToggle', script)
        self.assertIn('$("#sr-player-stop").onclick', script)
        # 卡片上的朗读键改成「从这条开始连读」，而不是只念这一条
        self.assertIn("playStart(+b.dataset.srRead)", script)
        # 暂停要真的能续上（两套播放器各自的暂停接口都得管）
        self.assertIn("speechSynthesis.pause()", script)
        self.assertIn("speechSynthesis.resume()", script)

        # ② 队列存事件 id：换筛选/切中英文都会重绘列表，下标会失效，id 不会
        self.assertIn("play.ids=state.items.map(c=>c.id)", script)
        self.assertIn("function playIndexOf", script)

        # ③ 停止 ≠ 快进：cancel() 会立刻触发上一条的 onend，必须靠代号作废那次回调
        self.assertIn("_ttsGen", script)
        self.assertIn("gen===_ttsGen", script)
        self.assertIn("_ttsGen++", script)

        # 播报条是老人版专属，且没有中文语音时连入口一起隐藏（优雅降级）
        self.assertIn("body.senior .sr-player", css)
        self.assertRegex(css, r"\.sr-player\{\s*display:none")
        self.assertIn("body.no-tts .sr-chip-play", css)
        self.assertIn(".ev.sr-now", css)          # 正在念的那条要看得出来
        self.assertIn("scrollIntoView", script)   # 并自动滚到眼前

    def test_two_axes_are_explained_and_never_merged(self):
        """存疑度和可信度必须在页面上被讲成两个轴，否则用户会当成同一套分级。"""
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        self.assertIn("存疑标注", html)
        self.assertIn("两个独立的轴", html)
        # 全部新闻都展示、存疑的只标不藏 —— 这是产品承诺，不能悄悄改成过滤。
        self.assertIn("存疑的也不隐藏", html)
        self.assertIn("doubt_detail", script)
        self.assertIn("存疑判定", script)

    def test_source_desk_explains_not_applicable_and_lang_bias(self):
        """治理台要说清『不适用』不是零分，以及低分语种是我方欠工。

        治理台已搬进管理后台，所以这些说明文案要跟着搬 —— 不能在搬家过程中丢掉。
        """
        script = (ROOT / "web" / "admin.js").read_text()
        self.assertIn("not_applicable", script)
        self.assertIn("无从抢首发", script)
        self.assertIn("别拿我们的欠工去降别人的档", script)
        # 定档门槛不能在前端写死一个数字，必须由后端带过来。
        self.assertIn("min_items_for_core", script)

    def test_focus_only_marks_curated_sources(self):
        """focus 缺省必须是 standard：126 个源里只有人工定过的才带这个字段。"""
        registry = json.loads((ROOT / "sources.json").read_text())
        values = {s["id"]: s["focus"] for s in registry["sources"] if "focus" in s}
        self.assertTrue(values, "至少应有人工定档过的源")
        self.assertTrue(set(values.values()) <= {"core", "standard", "probation"}, values)

    def test_global_wire_sources_enabled(self):
        """联合早报之外还要有独立于中国大陆媒体集团的通讯社。"""
        registry = json.loads((ROOT / "sources.json").read_text())
        by_id = {s["id"]: s for s in registry["sources"]}
        for sid in ("zaobao_realtime", "yonhap_cn", "kyodo_cn", "ansa_en"):
            self.assertIn(sid, by_id, sid)
            self.assertTrue(by_id[sid].get("enabled"), f"{sid} 应启用")
        owners = {by_id[s]["owner"] for s in ("zaobao_realtime", "yonhap_cn", "kyodo_cn")}
        self.assertEqual(len(owners), 3, "不同通讯社不能共用 owner，否则会被误算成独立印证")

    def test_zaobao_html_source_parses_realistic_links(self):
        registry = json.loads((ROOT / "sources.json").read_text())
        source = next(item for item in registry["sources"]
                      if item["id"] == "zaobao_realtime")
        body = ('<a href="/news/china/story20260907-9635867" '
                'title="广西南宁现场：当城市和政策成为传销话术的剧本素材">新闻</a>')
        items = parse_html(body, source)
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0]["published_ts"])
        self.assertEqual(items[0]["url"],
                         "https://www.zaobao.com.sg/news/china/story20260907-9635867")


if __name__ == "__main__":
    unittest.main()
