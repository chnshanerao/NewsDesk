"use strict";
// NEWSDESK 管理后台。与读者页（app.js）完全独立：不共享代码、不共享路由、不共享入口。
// 拆开的理由不是安全边界（数据本来公开只读、写操作一直由服务端令牌拦），而是这两类
// 使用者要看的东西根本不一样：读者要新闻，管理员要信源分档、门禁、账单和刷新日志。
// 混在一个界面里的结果是两边都被对方的控件干扰。
const $ = (s) => document.querySelector(s);
const TOKEN_KEY = "newsdeskWriteToken";

function esc(v){const d=document.createElement("div");d.textContent=v??"";return d.innerHTML.replace(/"/g,"&quot;")}
function num(v){return v==null?"—":Number(v).toLocaleString("zh-CN")}
function time(ts){return ts?new Date(ts*1000).toLocaleString("zh-CN",{hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"—"}
function pct(v){return v==null?"—":(100*v).toFixed(1)+"%"}
function toast(msg){const n=document.createElement("div");n.className="toast";n.textContent=msg;document.body.append(n);setTimeout(()=>n.remove(),2600)}

function token(){return sessionStorage.getItem(TOKEN_KEY)||""}

// 令牌走 header，不走 query string：URL 会进浏览器历史、Referer 和反代访问日志。
async function api(url,opt={}){
  const headers=new Headers(opt.headers||{});
  if(token())headers.set("X-Newsdesk-Token",token());
  const r=await fetch(url,{...opt,headers});
  if(r.status===401){lock("令牌已失效，请重新登录");throw new Error("401")}
  if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function lock(msg){
  sessionStorage.removeItem(TOKEN_KEY);
  $("#ad-shell").hidden=true;$("#ad-gate").hidden=false;
  $("#ad-reload").hidden=true;$("#ad-logout").hidden=true;
  if(msg){$("#ad-err").textContent=msg;$("#ad-err").hidden=false}
  $("#ad-token").value="";
}

function unlock(){
  $("#ad-gate").hidden=true;$("#ad-shell").hidden=false;
  $("#ad-reload").hidden=false;$("#ad-logout").hidden=false;
  $("#ad-err").hidden=true;
  render();
  loadUnread();
}

async function tryToken(raw){
  const t=(raw||"").trim();
  if(!t)return void(($("#ad-err").textContent="请输入令牌"),$("#ad-err").hidden=false);
  sessionStorage.setItem(TOKEN_KEY,t);
  $("#ad-enter").disabled=true;
  try{
    await api("/api/admin/verify");     // 只回 ok:true，不返回数据
    unlock();
  }catch(e){
    if(e.message!=="401")lock(`校验失败：${e.message}`);
  }finally{$("#ad-enter").disabled=false}
}

// ---------- 各面板 ----------
let section="overview";
const panels={};

const gradeLabels={core:"核心",support:"支撑",watch:"观察",prune:"待裁",dormant:"零产出"};

// 一次源的不适用项显示为「—」，而不是 0 —— 0 会被读成「它在这项上得了零分」。
function na(card,dim,value){
  if(!card)return 0;
  return card.not_applicable?.includes(dim)
    ? `<span class="na" title="此项对一次源不适用，不参与加权">—</span>`:value;
}

function card(label,value,sub,cls){
  return `<div class="ad-card ${cls||""}"><b>${value}</b><span>${esc(label)}</span>${sub?`<em>${esc(sub)}</em>`:""}</div>`;
}

panels.overview=async ()=>{
  const [st,q,tt,rv]=await Promise.all([
    api("/api/stats"),api("/api/quality"),api("/api/tts/usage"),
    api("/api/source-review").catch(()=>null)]);
  const ic=st.diversity_24h.independent_corroboration;
  const fails=q.gates.filter(g=>g.status==="fail"),warns=q.gates.filter(g=>g.status==="warn");
  const todo=[];
  if(fails.length)todo.push(`${fails.length} 项硬门禁失败：${fails.map(g=>g.name).join("、")}`);
  if(rv?.proposed_changes?.length)todo.push(`${rv.proposed_changes.length} 个信源的实际表现与当前分档不一致，待人工改 sources.json`);
  if(rv?.dormant_enabled?.length)todo.push(`${rv.dormant_enabled.length} 个源已启用但零产出，先分清是源停更还是本机抓不到`);
  if(!tt.enabled)todo.push("云端朗读未启用（缺 NEWSDESK_TTS=1 或 TTS key），老人版目前用浏览器自带语音");
  return `<h2>概览</h2>
  <div class="ad-cards">
    ${card("事件总数",num(st.clusters),`${num(st.items)} 篇入库`)}
    ${card("24h 新事件",num(st.fresh_24h),`噪音 ${num(st.noise_24h)}`)}
    ${card("24h 多源印证",num(ic.clusters),`占比 ${pct(ic.rate)} / 目标 10%`,ic.rate>=0.1?"ok":"warn")}
    ${card("质量门禁",fails.length?"有失败":warns.length?"有待提升":"全部通过",`${q.counts.pass} 通过 · ${q.counts.warn} 待提升 · ${q.counts.fail} 失败`,fails.length?"bad":warns.length?"warn":"ok")}
    ${card("今日朗读",`${num(tt.used_items)}/${num(tt.daily_items)}`,`¥${tt.spent_cny_today} / 上限 ¥${tt.cap_cny_per_day}`,tt.remaining_items?"":"warn")}
    ${card("最近一次抓取",st.last_run?time(st.last_run.started_ts):"—",st.last_run?`${num(st.last_run.n_items)} 条 · ${st.last_run.ok?"正常":"有错误"}`:"从未运行")}
  </div>
  <h3>待处理</h3>
  ${todo.length?todo.map(x=>`<div class="ad-todo">• ${esc(x)}</div>`).join(""):`<div class="note">没有需要人工介入的事项。</div>`}
  <h3>运行参数</h3>
  <table><tbody>
    <tr><th>关注画像</th><td>${esc(st.profile||"—")}（可信度门槛 ${st.min_credibility} · 相关度门槛 ${st.min_relevance}）</td></tr>
    <tr><th>LLM 甄别层</th><td>${st.llm_enabled?`已开启 · ${esc(st.llm_model)}`:"关闭"}</td></tr>
    <tr><th>朗读模型</th><td>${tt.enabled?`${esc(tt.model)} · 音色 ${esc(tt.voice)}`:"未启用（回退浏览器语音）"}</td></tr>
    <tr><th>集中度 (24h)</th><td>持有集团 HHI ${st.diversity_24h.owner.hhi} · 地区 HHI ${st.diversity_24h.region.hhi}</td></tr>
    <tr><th>服务器时间</th><td>${time(st.server_ts)}</td></tr>
  </tbody></table>`;
};

// 信源面板是『治理台』不是『健康表』：健康只答『抓得到吗』，治理还要答『值不值得留』。
panels.sources=async ()=>{
  const [d,rv]=await Promise.all([api("/api/sources"),api("/api/source-review").catch(()=>null)]);
  const vl={healthy:"健康",degraded:"降级",unhealthy:"解析异常",down:"不可达",disabled:"停用",unseen:"未检查"};
  const cards=Object.fromEntries((rv?.cards||[]).map(c=>[c.source_id,c]));
  const c=rv?.concentration;
  const under=Object.entries(rv?.lang_relevance||{}).filter(([,v])=>v.under_served).map(([k])=>k);
  const caveats=Object.fromEntries((rv?.proposed_changes||[]).filter(p=>p.caveat).map(p=>[p.source_id,p.caveat]));
  const head=rv?`<div class="src-summary"><b>${rv.n_sources}</b> 个信源 · 启用 <b>${rv.enabled}</b> · 窗口内有产出 <b>${rv.producing}</b>
    <div class="note">产出集中度：第一名 ${esc(c.top_source.source_id||"—")} 占 ${(100*(c.top_source.share||0)).toFixed(1)}%，前三占 ${(100*c.top3_share).toFixed(1)}%，最大集团 ${esc(c.top_group.group)} 占 ${(100*c.top_group.share).toFixed(1)}%，HHI ${c.hhi}。集中度越高，整个终端越接近单一口径。</div>
    ${rv.proposed_changes.length?`<div class="note">有 <b>${rv.proposed_changes.length}</b> 个源的实际表现与当前分档不一致；分档改动需人工确认后改 sources.json 的 focus 字段。</div>`:""}
    ${under.length?`<div class="note">⚠ 相关度偏低语种：<b>${under.map(esc).join(" / ")}</b> —— 关注画像目前只有中英文词表，这些语种分数低先记在我方账上，别拿我们的欠工去降别人的档。</div>`:""}
    ${rv.dormant_enabled.length?`<div class="note">有 <b>${rv.dormant_enabled.length}</b> 个源已启用但零产出 —— 先分清是源停更还是本机抓不到，再决定去留。</div>`:""}</div>`:`<div class="note">治理评分需要管理令牌；当前只显示抓取健康。</div>`;
  const sorted=[...d.sources].sort((a,b)=>(cards[b.id]?.score??-1)-(cards[a.id]?.score??-1));
  return `<h2>信源治理台</h2>${head}
  <table><thead><tr><th>评分</th><th>分档</th><th>裁决</th><th>层级</th><th>信源</th><th>角色</th><th>窗口条数</th><th>首发</th><th>被印证</th><th>存疑均值</th><th>说明</th></tr></thead><tbody>${sorted.map(s=>{const k=cards[s.id];const mismatch=k&&k.grade!==k.focus&&k.grade!=="dormant";return `<tr class="${s.enabled?"":"off"}"><td>${k?k.score.toFixed(3):"—"}</td><td class="${mismatch?"err":""}">${k?esc(gradeLabels[k.grade]||k.grade):"—"}${mismatch?`（当前 ${esc(gradeLabels[k.focus]||k.focus)}）`:""}</td><td class="${s.ok?"ok":"err"}">${vl[s.verdict]||esc(s.verdict)}</td><td>T${s.tier}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></td><td>${esc(s.source_role)}</td><td>${k?k.n_items:0}</td><td>${na(k,"lead",k?.n_lead)}</td><td>${na(k,"corroborated",k?.n_corroborated)}</td><td>${k?k.avg_doubt.toFixed(1):"—"}</td><td class="note">${esc(s.last_error||s.note||"")}${caveats[s.id]?`<div class="err">⚠ ${esc(caveats[s.id])}</div>`:""}</td></tr>`}).join("")}</tbody></table>
  <div class="note">评分 = 供稿 20% + 首发 20% + 被印证 15% + 相关 20% + 净度 15% + 低存疑 10%。供稿项按 log 压缩且 20 条封顶：一个源刷 500 条不会因为量大就被评成重要源，否则治理结论会变成『谁刷得多谁重要』，而集中度过高本身就是要治的毛病。</div>
  <div class="note">首发只在有别的集团也在报的事件里算数（没人跟不等于抢到独家）。一次源（官方声明、监管披露）的首发与被印证两项标为「—」：它自己就是当事人，无从抢首发，也不需要别人证明他是否这么说了 —— 这两项不参与它的加权，权重按比例摊到其余项。窗口内产出不足 ${rv?.min_items_for_core??"—"} 条的源不定档，样本太少时各项比率都是噪音。</div>`;
};

panels.quality=async ()=>{
  const d=await api("/api/quality"),lb={pass:"通过",warn:"待提升",fail:"失败"};
  return `<h2>质量门禁与产品边界</h2>
  <div class="quality-summary"><b>${d.status==="healthy"?"全部通过":d.status==="healthy_with_warnings"?"硬性门禁通过，仍有待提升项":"存在硬性失败"}</b><span>${d.counts.pass} 通过 · ${d.counts.warn} 待提升 · ${d.counts.fail} 失败</span></div>
  <div class="quality-gates">${d.gates.map(g=>`<div class="quality-gate ${g.status}"><span>${lb[g.status]}</span><b>${esc(g.name)}</b><code>${esc(g.value)} / ${esc(g.target)}</code><em>${esc(g.detail||"")}</em></div>`).join("")}</div>
  <h3>明确边界</h3>${d.boundaries.map(x=>`<div class="note">• ${esc(x)}</div>`).join("")}`;
};

panels.alerts=async ()=>{
  const [d,ev]=await Promise.all([api("/api/alerts"),api("/api/alert-events?limit=50")]);
  return `<h2>事件监控</h2>
  <div class="note">规则按关键词、主题、语言和证据门槛捕获每次刷新后新出现的事件。命中会写进下面的列表，配了 webhook 也会外推。</div>
  <div class="ad-form-row">
    <input class="search" id="al-name" placeholder="规则名称（必填）">
    <input class="search" id="al-q" placeholder="关键词，留空=全部">
    <input class="search ad-narrow" id="al-topic" placeholder="主题 id，默认 all">
    <input class="search ad-narrow" id="al-lang" placeholder="语言，默认 all">
    <input class="search ad-narrow" id="al-cred" type="number" min="0" max="100" placeholder="≥证据分">
    <button class="btn primary" id="al-save">新建规则</button>
  </div>
  <h3>监控规则</h3>
  <div class="alert-list">${d.alerts.length?d.alerts.map(a=>`<div class="alert-row"><div><b>${esc(a.name)}</b><span>${esc(a.q||"全部关键词")} · ${esc(a.topic)} · ${esc(a.lang)} · ≥${a.min_cred}</span></div><strong>${a.match_count_24h}</strong><button class="btn alert-delete" data-id="${a.id}">删除</button></div>`).join(""):`<div class="empty">尚无监控规则</div>`}</div>
  <h3>最近命中 ${ev.events.length?`<button class="btn" id="al-read">全部标已读</button>`:""}</h3>
  <div class="alert-events">${ev.events.length?ev.events.map(x=>`<div class="alert-event ${x.read_ts?"read":""}"><span>${esc(x.alert_name)}</span><b>${esc(x.headline)}</b><em>${Math.round(x.cred)}分 · ${time(x.event_ts)}</em></div>`).join(""):`<div class="empty">还没有命中记录</div>`}</div>`;
};
panels.alerts.wire=()=>{
  $("#al-save").onclick=async()=>{
    const name=$("#al-name").value.trim();
    if(!name)return toast("请输入规则名称");
    try{
      await api("/api/alerts",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({name,q:$("#al-q").value.trim(),
          topic:$("#al-topic").value.trim()||"all",lang:$("#al-lang").value.trim()||"all",
          min_cred:Number($("#al-cred").value||0)})});
      toast("已新建规则");render();
    }catch(e){toast(`失败：${e.message}`)}
  };
  if($("#al-read"))$("#al-read").onclick=async()=>{
    await api("/api/alert-events/read",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    toast("已全部标记已读");loadUnread();render();
  };
  document.querySelectorAll(".alert-delete").forEach(b=>b.onclick=async()=>{
    if(!confirm("删除这条监控规则？已产生的命中记录会保留。"))return;
    await api(`/api/alerts/${b.dataset.id}`,{method:"DELETE"});toast("已删除");render();
  });
};

panels.watchlist=async ()=>{
  const d=await api("/api/watchlist");
  return `<h2>资产观察列表</h2>
  <div class="note">观察列表是全站唯一一份（单租户部署），所以放在管理后台维护；读者页顶部的行情条会读同一份数据。</div>
  <div class="ad-form-row"><input class="search" id="wl-sym" placeholder="资产代码，如 BTC-USD / 000001.SS"><button class="btn primary" id="wl-add">加入</button></div>
  <table><thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>类别</th><th></th></tr></thead><tbody>${d.items.length?d.items.map(x=>`<tr><td>${esc(x.symbol)}</td><td>${esc(x.market?.name||"—")}</td><td>${x.market?Number(x.market.price).toLocaleString("zh-CN",{maximumFractionDigits:4}):"无报价"}</td><td>${esc(x.market?.asset||"—")}</td><td><button class="btn wl-del" data-sym="${esc(x.symbol)}">移出</button></td></tr>`).join(""):`<tr><td colspan="5" class="note">列表为空</td></tr>`}</tbody></table>`;
};
panels.watchlist.wire=()=>{
  $("#wl-add").onclick=async()=>{
    const symbol=$("#wl-sym").value.trim();
    if(!symbol)return toast("请输入资产代码");
    try{await api("/api/watchlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol})});toast("已加入");render()}
    catch(e){toast(`失败：${e.message}`)}
  };
  document.querySelectorAll(".wl-del").forEach(b=>b.onclick=async()=>{
    await api(`/api/watchlist/${encodeURIComponent(b.dataset.sym)}`,{method:"DELETE"});toast("已移出");render();
  });
};

// 朗读用量：TTS 按字符计费，不按 token。所以这里两道闸都要看得见。
panels.voice=async ()=>{
  const t=await api("/api/tts/usage");
  const usedP=t.daily_items?Math.min(100,100*t.used_items/t.daily_items):0;
  const usedC=t.daily_chars?Math.min(100,100*t.used_chars/t.daily_chars):0;
  return `<h2>朗读用量与预算</h2>
  <div class="ad-cards">
    ${card("云端朗读",t.enabled?"已启用":"未启用",t.enabled?`${esc(t.model)} · ${esc(t.voice)}`:"回退浏览器自带语音",t.enabled?"ok":"warn")}
    ${card("今日条数",`${num(t.used_items)}/${num(t.daily_items)}`,`剩 ${num(t.remaining_items)} 条`)}
    ${card("今日字符",`${num(t.used_chars)}/${num(t.daily_chars)}`,`剩 ${num(t.remaining_chars)} 字符`)}
    ${card("今日花费",`¥${t.spent_cny_today}`,`当天上限 ¥${t.cap_cny_per_day}`)}
  </div>
  <div class="ad-meter"><span>条数</span><div class="ad-meter-bar"><i style="width:${usedP.toFixed(1)}%"></i></div><b>${usedP.toFixed(0)}%</b></div>
  <div class="ad-meter"><span>字符</span><div class="ad-meter-bar"><i style="width:${usedC.toFixed(1)}%"></i></div><b>${usedC.toFixed(0)}%</b></div>
  <h3>上限是怎么定的</h3>
  <div class="note">TTS <b>按字符计费</b>，不按 token（单价 ¥${t.price_cny_per_10k_chars}/万字符）。两道每日闸门同时生效，
    先撞到哪道就停：① 每天最多 <b>${num(t.daily_items)}</b> 条（你定的数）；② 每天最多 <b>${num(t.daily_chars)}</b> 个字符
    （= ${num(t.daily_items)} 条 × 单条 ${num(t.max_chars_per_call)} 字满配）。只留条数上限，「每条都读全文」会超支；
    只留字符上限，「每条都极短」会被刷成上千次请求。两道都留着，任何使用形态都封得住顶：
    最坏情况一天 <b>¥${t.cap_cny_per_day}</b>，一个月不超过 <b>¥${(t.cap_cny_per_day*30).toFixed(1)}</b>。</div>
  <div class="note">同一段文字重复朗读命中本地音频缓存：<b>不计费、不占名额</b>。撞上限、缺 key 或上游报错时接口返回 ok=false，
    前端静默回退到浏览器自带语音 —— 读者只会觉得声音换了，不会遇到「朗读坏了」。</div>
  ${t.enabled?"":`<div class="ad-todo">要启用云端音色：在服务器 <code>/opt/newsdesk/newsdesk.env</code> 里加 <code>NEWSDESK_TTS=1</code> 和一把支持 TTS 模型的 <code>NEWSDESK_TTS_KEY</code>，然后 <code>systemctl restart newsdesk</code>。密钥不进代码库、不进聊天记录。</div>`}`;
};

panels.translate=async ()=>{
  const d=await api("/api/translate-usage?days=7");
  const rows=d.rows||[];
  return `<h2>翻译 token 账单（近 ${d.days} 天）</h2>
  ${rows.length?`<div class="ad-cards">
    ${card("总调用",num(d.total.calls))}
    ${card("输入 token",num(d.total.prompt))}
    ${card("输出 token",num(d.total.completion))}
    ${card("合计 token",num(d.total.tokens))}
  </div>
  <table><thead><tr><th>用途</th><th>模型</th><th>调用</th><th>输入 token</th><th>输出 token</th><th>首次</th><th>最近</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.kind)}</td><td>${esc(r.model)}</td><td>${num(r.calls)}</td><td>${num(r.prompt)}</td><td>${num(r.completion)}</td><td>${time(r.first_ts)}</td><td>${time(r.last_ts)}</td></tr>`).join("")}</tbody></table>`
  :`<div class="note">还没有翻译用量记录（translate_usage 表由 translate.py 首次翻译时懒建）。</div>`}
  <div class="note">这张表用来核对「预估 vs 实际」。历史教训：上一轮正文翻译的估算偏低约 30%（估 750、实际 1068 token/篇），
    所以成本结论一律以这里的实测数为准，不用估算数对外汇报。</div>`;
};

panels.ops=async ()=>{
  const [s,cl]=await Promise.all([api("/api/refresh_status"),api("/api/changelog").catch(()=>[])]);
  return `<h2>刷新与运维</h2>
  <div class="ad-form-row"><button class="btn primary" id="op-refresh" ${s.running?"disabled":""}>${s.running?"正在刷新…":"立即刷新全部信源"}</button>
    <span class="note">上次结束：${s.finished?time(s.finished):"—"}${s.error?` · 错误：${esc(s.error)}`:""}</span></div>
  ${s.log?`<pre class="ad-log">${esc(Array.isArray(s.log)?s.log.join("\n"):String(s.log))}</pre>`:""}
  <h3>版本更新记录</h3>
  <div class="changelog">${(cl||[]).length?cl.map(e=>`<div class="changelog-entry"><div class="changelog-head"><b>v${esc(e.version)}</b><span>${esc(e.date)}</span><em>${esc(e.title)}</em></div><ul>${e.changes.map(c=>`<li>${esc(c)}</li>`).join("")}</ul></div>`).join(""):`<div class="note">暂无更新日志</div>`}</div>`;
};
panels.ops.wire=()=>{
  const b=$("#op-refresh");if(!b)return;
  b.onclick=async()=>{
    b.disabled=true;b.textContent="正在刷新…";
    try{
      await api("/api/refresh",{method:"POST"});
      const t=setInterval(async()=>{
        const s=await api("/api/refresh_status");
        if(!s.running){clearInterval(t);toast(s.error?`刷新失败：${s.error}`:"刷新完成");render()}
      },1500);
    }catch(e){b.disabled=false;b.textContent="立即刷新全部信源";toast(`失败：${e.message}`)}
  };
};

// 引用研究台：抽取式检索，每条结论必须绑定原始引文。放后台是因为它是编辑工具，不是读者功能。
panels.research=async ()=>`<h2>引用研究台</h2>
  <div class="ad-form-row"><input class="search" id="rs-q" placeholder="例如：过去一周 AI 芯片有哪些重要进展？"><button class="btn primary" id="rs-run">检索证据</button></div>
  <div class="note">抽取式研究：每条事实必须绑定原始标题或摘要原句，不让模型补写无引用结论。</div>
  <div id="rs-out"></div>`;
panels.research.wire=()=>{
  const run=async()=>{
    const q=$("#rs-q").value.trim(),box=$("#rs-out");
    if(q.length<2){box.innerHTML='<div class="note">请输入至少两个字符。</div>';return}
    box.innerHTML='<div class="note">正在检索 claim 证据库…</div>';
    const d=await api("/api/research?q="+encodeURIComponent(q)+"&limit=10"),
      lb={independently_reported:"独立印证",official_statement:"机构声明",single_report:"单源待证",disputed:"存在反向证据"},
      rels={support:"支持",refute:"反驳",unknown:"待判定"};
    if(!d.findings.length){box.innerHTML='<div class="empty">没有找到足够接近且带原始引文的结论。请换用公司、模型或技术关键词。</div>';return}
    box.innerHTML='<div class="research-summary">'+d.findings.length+' 条可引用结论 · 引用覆盖率 '+Math.round(d.citation_coverage*100)+'%</div>'
      +d.findings.map((x,i)=>'<article class="research-finding"><div class="research-number">'+String(i+1).padStart(2,"0")+'</div><div><span class="claim-status '+esc(x.status)+'">'+esc(lb[x.status]||x.status)+'</span><p>'+esc(x.sentence)+'</p><div class="research-cites">'+x.citations.map((c,j)=>'<a href="'+esc(c.url)+'" target="_blank" rel="noopener noreferrer"><b><span class="claim-relation '+esc(c.relation||"support")+'">'+esc(rels[c.relation||"support"])+'</span>['+(j+1)+'] '+esc(c.source)+'</b><q>'+esc(c.quote)+'</q><small>'+esc(c.quote_field)+' '+c.quote_start+'–'+c.quote_end+' · '+esc(c.quote_hash)+'</small></a>').join("")+'</div></div></article>').join("")
      +'<div class="research-limits">'+d.limitations.map(x=>'<div>• '+esc(x)+'</div>').join("")+'</div>';
  };
  $("#rs-run").onclick=run;
  $("#rs-q").onkeydown=e=>{if(e.key==="Enter")run()};
  $("#rs-q").focus();
};

async function render(){
  const main=$("#ad-main"),fn=panels[section]||panels.overview;
  main.innerHTML='<div class="note">载入中…</div>';
  try{
    main.innerHTML=await fn();
    if(fn.wire)fn.wire();
  }catch(e){
    if(e.message==="401")return;
    main.innerHTML=`<div class="ad-todo">载入失败：${esc(e.message)}</div>`;
  }
}

async function loadUnread(){
  try{
    const d=await api("/api/alert-events?unread=1&limit=100");
    const b=$("#ad-unread");b.textContent=d.unread;b.hidden=!d.unread;
  }catch(_){}
}

document.querySelectorAll(".ad-nav-item[data-sec]").forEach(b=>b.onclick=()=>{
  section=b.dataset.sec;
  document.querySelectorAll(".ad-nav-item[data-sec]").forEach(x=>x.classList.toggle("on",x===b));
  render();
});
$("#ad-form").onsubmit=e=>{e.preventDefault();tryToken($("#ad-token").value)};
$("#ad-reload").onclick=()=>{render();loadUnread()};
$("#ad-logout").onclick=()=>lock("已退出管理");
setInterval(()=>$("#ad-clock").textContent=new Date().toLocaleTimeString("zh-CN",{hour12:false}),1000);

// 会话里已有令牌就直接验一次进去：刷新页面不该要求重新输入，但关掉标签页就得重来。
if(token())tryToken(token());else $("#ad-token").focus();
