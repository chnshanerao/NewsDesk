"use strict";

const $ = (s) => document.querySelector(s);
const state = {view: "signal", mode: "personal", focus: "all", topic: "all", lang: "all", hours: 48, order: "rank", minCred: 0,
               items: [], selected: -1, profile: null,
               // 展示语言：zh=默认全中文（有译文就用译文），orig=原文。仅影响显示，不改数据。
               disp: localStorage.getItem("nd_disp") || "zh"};
// 取展示标题：中文模式且有中文译文则用译文，否则回退原文（含尚未译好的外文）。
function hl(c){ return state.disp==="zh" && c && c.headline_zh ? c.headline_zh : (c?c.headline:""); }
const labels = {CONFIRMED: "✅ 多集团报道", LIKELY: "🟢 证据较完整",
                SINGLE: "🟡 单源待证", LOW: "🔴 证据较弱"};
// 存疑度是独立于可信度的第二个轴：可信度看『证据有多完整』，存疑度只数『主动的可疑信号』。
// 一条只有单一官方来源的公报可信度不高但并不可疑；一条被十家转载的『暴涨』稿反之。
const doubtLabels = {SUSPECT:"⚠ 高度存疑", QUESTIONABLE:"◍ 有存疑点",
                     MINOR:"· 轻微存疑", CLEAR:""};
// 老人版大白话：把「可信度 / 存疑 / 单源」这类术语换成「几家媒体在说 / 还没证实」。
// 只在 body.senior 时启用，普通模式仍用上面精确的术语（两版面向不同读者）。
const srLabels = {CONFIRMED:"✅ 好多家媒体在报", LIKELY:"🟢 有几家媒体在说",
                  SINGLE:"🟡 目前只有一家说", LOW:"🔴 还说不准，先别全信"};
const srDoubtShort = {SUSPECT:"⚠ 有可疑", QUESTIONABLE:"◍ 有没说清的",
                      MINOR:"· 有点疑问", CLEAR:""};
const srDoubtFull = {SUSPECT:"有明显可疑的地方", QUESTIONABLE:"有些地方还没说清楚",
                     MINOR:"有一点小疑问", CLEAR:"没发现可疑的地方"};
// 取当前应展示的档位标签：老人版用大白话，普通版用术语。
function credLabel(code){ return isSenior() ? (srLabels[code]||labels[code]) : (labels[code]||""); }
function doubtLabel(code){ return isSenior() ? (srDoubtShort[code]||"") : (doubtLabels[code]||""); }
const doubtColor = {SUSPECT:"var(--critical)", QUESTIONABLE:"var(--serious)",
                    MINOR:"var(--warn)", CLEAR:"var(--muted)"};
const topicLabels = {macro:"宏观", policy:"政策", market:"市场", tech:"科技综合",
  ai_research:"AI研究", ai_models:"模型/产品", ai_compute:"算力/芯片",
  ai_open_source:"开源生态", ai_governance:"AI治理", ai_industry:"AI产业",
  career:"就业", industry:"产业", risk:"风险", world:"国际", society:"民生"};

// 来源类别用于解释“证据来自哪里”，不是对单篇报道真假的裁决。
function sourceKind(x) {
  if(x.source_role==="official") return {key:"official",label:"官方 / 机构声明",hint:"一手发布；仍需区分声明、数据与已验证事实"};
  if(["aggregator","community"].includes(x.source_role)) return {key:"lead",label:x.source_role==="community"?"社区线索":"聚合线索",hint:"用于发现议题，不视为独立印证"};
  const hay=`${x.source_name||""} ${x.grp||""} ${x.url||""}`.toLowerCase();
  const official=/(gov\.cn|\.gov\/|\.gov$|un\.org|who\.int|worldbank\.org|imf\.org|ecb\.europa\.eu|federalreserve\.gov|sec\.gov|政府|国务院|统计局|央行|人民银行|证监会|交易所|联合国|美联储|欧洲央行|机构公告)/;
  if(official.test(hay)) return {key:"official",label:"官方 / 机构声明",hint:"一手发布；仍需区分声明、数据与已验证事实"};
  if(Number(x.tier)>=3) return {key:"lead",label:Number(x.tier)>=4?"社区线索":"聚合线索",hint:"用于发现议题，不视为独立印证"};
  return {key:"media",label:"独立媒体报道",hint:"媒体报道；独立性按媒体集团去重"};
}

function esc(v) { const d=document.createElement("div"); d.textContent=v??""; return d.innerHTML.replace(/"/g,"&quot;"); }
function time(ts) { return ts ? new Date(ts*1000).toLocaleString("zh-CN", {hour12:false, month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—"; }
// 读者页不再向任何人索要管理令牌：管理动作已搬到 /admin.html。这里 401 就是 401，
// 由调用方给出一句人话提示，不弹「请输入令牌」去为难普通读者。
async function api(url,opt={}){const headers=new Headers(opt.headers||{});const r=await fetch(url,{...opt,headers});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return r.json()}
function toast(msg) { const n=document.createElement("div"); n.className="toast"; n.textContent=msg; document.body.append(n); setTimeout(()=>n.remove(),2600); }
window.addEventListener("unhandledrejection",event=>{const message=event.reason?.message||String(event.reason||"未知错误");toast(`操作失败：${message}`);event.preventDefault()});
function color(code) { return ({CONFIRMED:"var(--good)",LIKELY:"var(--warn)",SINGLE:"var(--serious)",LOW:"var(--critical)"})[code]; }
function closeDetail(){state.selected=-1;document.querySelectorAll(".ev").forEach(n=>n.classList.remove("sel"));$("#detail").className="detail empty";$("#detail").innerHTML="选中一条事件<br>查看来源构成、证据与未知项"}

async function loadStats() {
  const [s,q]=await Promise.all([api("/api/stats"),api("/api/quality")]);
  const b=s.bands||{}, total=Object.values(b).reduce((a,x)=>a+x,0)||1;
  const sl=s.source_languages||{}, sourceTotal=Object.values(sl).reduce((a,x)=>a+x,0)||0;
  $("#kpis").innerHTML=`
    <div class="kpi"><div class="k">近24H 事件</div><div class="v amber">${s.fresh_24h}</div><div class="s">库内 ${s.clusters}</div></div>
    <div class="kpi"><div class="k">多源印证</div><div class="v green">${s.multi_source_24h}</div><div class="s">独立集团 ≥ 2</div></div>
    <div class="kpi"><div class="k">待过滤噪音</div><div class="v red">${s.noise_24h}</div><div class="s">低相关或低可信</div></div>
    <div class="kpi"><div class="k">证据分布</div><div class="v blue">${b.CONFIRMED||0}/${total}</div><div class="dist">${["CONFIRMED","LIKELY","SINGLE","LOW"].map(x=>`<i style="width:${(b[x]||0)/total*100}%;background:${color(x)}"></i>`).join("")}</div></div>
    <div class="kpi"><div class="k">外语信源</div><div class="v blue">${(sl.en||0)+(sl.pt||0)}/${sourceTotal}</div><div class="s">近24H EN ${s.item_languages_24h?.en||0} · PT ${s.item_languages_24h?.pt||0}</div></div>
    <div class="kpi quality-kpi" data-quality-open title="查看全部质量门禁"><div class="k">质量门禁</div><div class="v ${q.counts.fail?'red':q.counts.warn?'yellow':'green'}">${q.counts.pass}/${q.counts.pass+q.counts.warn+q.counts.fail}</div><div class="s">${q.counts.fail} 失败 · ${q.counts.warn} 待提升</div></div>
    <div class="kpi"><div class="k">上次刷新</div><div class="v" style="font-size:13px">${s.last_run?time(s.last_run.ended_ts):"尚未运行"}</div><div class="s">${s.refresh.running?"正在抓取…":s.llm_enabled?`LLM ${s.llm_model} · 已甄别 ${s.llm_judged}`:"LLM 关闭 · 规则模式"}</div></div>`;
  document.querySelector("[data-quality-open]").onclick=showQuality;
  renderTopics(s.topics_24h||{});
  return s;
}

async function loadMarkets() {
  try {
    const d=await api("/api/markets");
    const rows=d.instruments||[];
    $("#markets").innerHTML=`<b>MARKETS <small>DELAYED</small></b>${rows.map(x=>{const ch=x.change_pct,bp=x.change_bps,delta=ch!=null?ch:bp,cls=delta==null?"flat":delta>=0?"up":"down";return `<button class="market" data-market="${esc(x.symbol)}"><i>${esc(x.name)}</i><strong>${Number(x.price).toLocaleString("zh-CN",{maximumFractionDigits:4})}${x.asset==="rates"?"%":""}</strong>${ch!=null?`<em class="${cls}">${ch>=0?"+":""}${ch.toFixed(2)}%</em>`:bp!=null?`<em class="${cls}">${bp>=0?"+":""}${bp.toFixed(1)}bp</em>`:""}</button>`}).join("")}<span class="market-note" title="${esc(d.disclaimer)}">仅供背景参考</span>`;
    document.querySelectorAll("[data-market]").forEach(b=>b.onclick=()=>showMarket(b.dataset.market));
  } catch(e) { $("#markets").innerHTML=`<span class="market-loading">市场数据暂不可用</span>`; }
}

function renderTopics(counts) {
  const all=Object.values(counts).reduce((a,x)=>a+x,0);
  $("#topics").innerHTML=[["all","全部",all],...Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[k,topicLabels[k]||k,v])]
    .map(([k,l,n])=>`<button class="chip ${state.topic===k?"on":""}" data-topic="${k}">${esc(l)}<em>${n}</em></button>`).join("");
  document.querySelectorAll("[data-topic]").forEach(b=>b.onclick=()=>{state.topic=b.dataset.topic;document.querySelectorAll("[data-topic]").forEach(x=>x.classList.toggle("on",x.dataset.topic===state.topic));loadFeed();});
}

function initFilters() {
  $("#focus-chips").innerHTML=[["all","综合资讯"],["balanced","平衡"],["ai","AI 专注"]].map(([v,l])=>`<button class="chip ${state.focus===v?"on":""}" data-focus="${v}">${l}</button>`).join("");
  $("#hours").innerHTML=[[24,"24 小时"],[48,"48 小时"],[168,"7 天"]].map(([v,l])=>`<button class="chip ${state.hours===v?"on":""}" data-hours="${v}">${l}</button>`).join("");
  $("#languages").innerHTML=[["all","多语言混排"],["zh","仅中文"],["en","仅 English"],["pt","Português"]].map(([v,l])=>`<button class="chip ${state.lang===v?"on":""}" data-lang="${v}">${l}</button>`).join("");
  $("#orders").innerHTML=[["rank","综合价值"],["time","最新发布"]].map(([v,l])=>`<button class="chip ${state.order===v?"on":""}" data-order="${v}">${l}</button>`).join("");
  document.querySelectorAll("[data-focus]").forEach(b=>b.onclick=()=>{state.focus=b.dataset.focus;initFilters();loadFeed();});
  document.querySelectorAll("[data-hours]").forEach(b=>b.onclick=()=>{state.hours=+b.dataset.hours;initFilters();loadFeed();});
  document.querySelectorAll("[data-lang]").forEach(b=>b.onclick=()=>{state.lang=b.dataset.lang;initFilters();loadFeed();});
  document.querySelectorAll("[data-order]").forEach(b=>b.onclick=()=>{state.order=b.dataset.order;initFilters();loadFeed();});
}

const queryLabels={text:"关键词",source:"信源",lang:"语言",topic:"主题",asset:"资产",min_cred:"证据分",hours:"时间"};
function removeQueryPart(key) {
  const raw=$("#q").value;
  if(key==="text") {
    // Preserve recognized operators while clearing only free-text terms.
    $("#q").value=(raw.match(/(?:[^\s"']+:"[^"]*"|[^\s"']+:'[^']*'|[^\s"']+:[^\s"']+)/g)||[]).join(" ");
  } else {
    const operator={min_cred:"cred",hours:"after"}[key]||key;
    const re=new RegExp(`(^|\\s)${operator}:(?:"[^"]*"|'[^']*'|\\S+)`,"ig");
    $("#q").value=raw.replace(re," ").replace(/\s+/g," ").trim();
  }
  loadFeed();
}
function renderParsedQuery(parsed) {
  const box=$("#query-summary"), parts=[];
  if(parsed.text) parts.push(["text",parsed.text]);
  if(parsed.source) parts.push(["source",parsed.source]);
  if(parsed.lang&&parsed.lang!=="all") parts.push(["lang",parsed.lang.toUpperCase()]);
  if(parsed.topic) parts.push(["topic",topicLabels[parsed.topic]||parsed.topic]);
  if(parsed.asset) parts.push(["asset",parsed.asset]);
  if(parsed.min_cred!=null) parts.push(["min_cred",`≥ ${parsed.min_cred}`]);
  if(parsed.hours!=null) parts.push(["hours",parsed.hours%24===0?`${parsed.hours/24} 天`:`${parsed.hours} 小时`]);
  box.hidden=!parts.length;
  box.innerHTML=parts.length?`<b>当前检索</b>${parts.map(([k,v])=>`<button data-query-part="${k}" title="移除该条件"><span>${queryLabels[k]}</span>${esc(v)} <i>×</i></button>`).join("")}<span class="query-help">source:Reuters lang:en topic:macro asset:usINX cred:&gt;=60 after:2d</span>`:"";
  box.querySelectorAll("[data-query-part]").forEach(b=>b.onclick=()=>removeQueryPart(b.dataset.queryPart));
}

async function loadFeed() {
  // 换筛选/搜索 = 换了一份列表，正在连读的队列已经不是用户面前这一页了，先停下来。
  if(play.on)playStop(true);
  const p=new URLSearchParams({view:state.view,hours:state.hours,lang:state.lang,order:state.order,min_cred:state.minCred,limit:120});
  // 公共全景显式解除画像相关性门槛；个人模式让服务端采用当前画像门槛。
  if(state.mode==="public") p.set("min_rel","0");
  if(state.focus!=="all") p.set("focus",state.focus);
  if(state.topic!=="all") p.set("topic",state.topic); if($("#q").value.trim()) p.set("q",$("#q").value.trim());
  const d=await api("/api/feed?"+p); state.items=d.items; state.selected=-1;
  renderParsedQuery(d.parsed_query||{});
  const searched=$("#q").value.trim()?" · 检索结果":"";
  $("#count").textContent=`${d.total} 个事件 · ${state.view==="signal"?"值得看":state.view==="unverified"?"待证":"噪音"}${searched}`;
  renderRows();
}
// 列表渲染独立成函数：切换中文/原文时无需重新拉取，直接用 state.items 重绘。
function renderRows() {
  const senior=isSenior();
  $("#rows").innerHTML=state.items.length?state.items.map((c,i)=>`<article class="ev${i===state.selected?" sel":""}" data-i="${i}" tabindex="0">
    <div class="ts">${time(c.last_ts)}</div><div class="cr" style="color:${color(c.cred_code)}">${Math.round(c.cred)}</div>
    <div class="body"><div class="hl">${esc(hl(c))}</div><div class="meta"><span class="badge b-${c.cred_code}">${credLabel(c.cred_code)}</span><span class="tier t${c.best_tier}">T${c.best_tier}</span><span class="src">${esc(c.headline_src)}</span><span class="gcount">${senior?`${c.n_groups} 家媒体在说`:`${c.n_groups}源/${c.n_items}篇`}</span>${(c.languages||[]).map(x=>`<span class="tier">${esc(x.toUpperCase())}</span>`).join("")}${c.topics.map(t=>`<span class="topic">#${esc(topicLabels[t]||t)}</span>`).join("")}${c.doubt_code&&c.doubt_code!=="CLEAR"?`<span class="doubt d-${c.doubt_code}" title="存疑度 ${c.doubt}/100：${esc((c.doubt_detail?.reasons||[]).map(r=>r.reason).join("；"))}">${doubtLabel(c.doubt_code)} ${senior?"":Math.round(c.doubt)}</span>`:""}${c.llm?.red_flags?.length?`<span class="flag">⚑${c.llm.red_flags.length}</span>`:""}</div>${senior?`<button class="sr-read" data-sr-read="${i}" title="从这条开始，一条接一条念下去">🔊 从这条开始念</button>`:""}<div class="meter"><i style="width:${c.cred}%;background:${color(c.cred_code)}"></i></div></div></article>`).join(""):`<div class="note" style="padding:30px;text-align:center">当前筛选条件下没有事件</div>`;
  document.querySelectorAll(".ev").forEach(n=>n.onclick=()=>select(+n.dataset.i));
  // 卡片上的朗读键改成「从这条开始连读」：老人的诉求是听下去，不是听一条。
  document.querySelectorAll("[data-sr-read]").forEach(b=>b.onclick=e=>{e.stopPropagation();playStart(+b.dataset.srRead);});
  playPaint();   // 重绘后把高亮/播报条状态贴回去
}
// 中文 / 原文 切换：只改展示，不重新拉数据。重绘列表并重开当前详情（详情会重新取 *_zh）。
function syncDispBtn(){const b=$("#btn-lang");if(!b)return;b.textContent=state.disp==="zh"?"🌐 原文":"🌐 中文";b.classList.toggle("on",state.disp==="orig");b.title=state.disp==="zh"?"当前：默认全中文 · 点击查看原文":"当前：显示原文 · 点击切回中文";}
function toggleDisp(){state.disp=state.disp==="zh"?"orig":"zh";localStorage.setItem("nd_disp",state.disp);syncDispBtn();renderRows();if(state.selected>=0)select(state.selected);}

// 管理界面已完全搬到独立页面 /admin.html（令牌登录后才拉数据）。
// 这个页面从此只有读者视角：没有信源治理、监控规则、刷新、令牌解锁等任何管理入口，
// 两类使用者不再混在同一个界面里。数据本身仍是公开只读的，写操作由服务端令牌拦。

// ---- 老人版模式 ----
// 老人版不是「把暗色终端调亮」——那只是换配色，密排三栏还在，字再大也难读。
// 这一版是独立的阅读版式：隐掉侧栏/KPI/行情条，单栏居中卡片流，圆形可信度徽标，
// 衬线大标题，一条一个大朗读按钮，详情整屏展开。开关与字号只存 localStorage，
// 纯前端，不动数据也不动服务端。
function isSenior(){return localStorage.getItem("nd_senior")==="1";}
// 字号三档：1=大(默认) 2=更大 3=特大。老人视力差异极大，一个固定字号覆盖不了。
const SR_SIZES=["大","更大","特大"];
function srSize(){const n=parseInt(localStorage.getItem("nd_srsize")||"1",10);return Math.min(3,Math.max(1,n||1));}
function applySrSize(){
  document.body.dataset.srsize=String(srSize());
  const v=$("#sr-font-val");if(v)v.textContent=SR_SIZES[srSize()-1];
}
function bumpSrSize(delta){
  const next=Math.min(3,Math.max(1,srSize()+delta));
  if(next===srSize())return toast(delta>0?"已经是最大字号了":"已经是最小字号了");
  localStorage.setItem("nd_srsize",String(next));applySrSize();
}
function syncSeniorBtn(){const b=$("#btn-senior");if(!b)return;const on=isSenior();b.textContent=on?"👓 标准版":"👓 老人版";b.classList.toggle("on",on);b.title=on?"当前：老人版（大字·朗读）· 点击切回标准版":"切换到老人版：大字、浅色护眼、可朗读";}
function applySenior(){document.body.classList.toggle("senior",isSenior());applySrSize();syncSeniorBtn();}
function toggleSenior(){
  const on=!isSenior();
  if(on)localStorage.setItem("nd_senior","1");else localStorage.removeItem("nd_senior");
  if(!on)playStop(true);   // 回标准版就别继续念了，播报条也随之收起
  applySenior();closeDetail();renderRows();
  window.scrollTo({top:0});$("#stream")?.scrollTo({top:0});
  toast(on?"已开启老人版：大字、护眼配色、可朗读":"已切回标准版");
}
// 首次访问轻问一句要不要开老人版（用独立 flag，问过一次就不再打扰）。
function askSenior(){
  if(isSenior()||localStorage.getItem("nd_seniorAsked"))return;
  localStorage.setItem("nd_seniorAsked","1");
  const m=modal("需要老人版吗？",`<div class="sr-ask"><p>老人版会把新闻字调大、换成浅色高对比，并给每条加一个「🔊 朗读」按钮，用你设备自带的中文语音念出来。</p><p class="sr-ask-note">完全免费、不联网、随时可关。适合自己看，也适合念给家里长辈听。</p><div class="sr-ask-btns"><button class="btn sr-ask-no">先不用</button><button class="btn sr-ask-yes">开启老人版</button></div></div>`,"sr-ask-modal");
  m.querySelector(".sr-ask-no").onclick=()=>m.remove();
  m.querySelector(".sr-ask-yes").onclick=()=>{m.remove();localStorage.setItem("nd_senior","1");applySenior();renderRows();toast("已开启老人版");};
}

// ---- 朗读（TTS）----
// 两层：① 云端合成（音色自然，按字符计费，服务端每天 100 条 + 6 万字符双上限）；
//      ② 浏览器自带 Web Speech API（零成本、纯本地）。
// 云端只在服务端开了开关且当天额度还有的时候用；额度用尽、未配置、请求失败一律
// 静默退回 ②，读者只会觉得声音换了，不会遇到「朗读坏了」。
// 连 ② 都没有中文语音的浏览器加 body.no-tts，朗读按钮自动隐藏。
let _ttsVoices=[];
let _cloudTts=null;      // null=未探测；对象=服务端额度快照
let _cloudAudio=null;    // 正在播的 <audio>
async function ttsProbeCloud(){
  try{ const d=await api("/api/tts/usage"); _cloudTts=(d&&d.enabled&&d.remaining_items>0&&d.remaining_chars>0)?d:false; }
  catch(_){ _cloudTts=false; }
  ttsRefreshVoices();   // 云端结论会改变「要不要隐藏朗读按钮」的判断
}
function cloudStop(){ if(_cloudAudio){_cloudAudio.pause();_cloudAudio.src="";_cloudAudio=null;} }
// 返回 true 表示云端接手了；false 表示调用方该走浏览器语音。
async function cloudSpeak(text,btn,done){
  if(_cloudTts===false||_cloudTts===null)return false;
  const gen=_ttsGen;   // 合成要等网络往返，期间用户可能已按停止/跳条
  try{
    const r=await api("/api/tts",{method:"POST",headers:{"Content-Type":"application/json"},
                                 body:JSON.stringify({text})});
    if(r.usage)_cloudTts=(r.usage.remaining_items>0&&r.usage.remaining_chars>0)?r.usage:false;
    if(gen!==_ttsGen)return true;             // 已被停掉：别再出声，也别退回浏览器语音
    if(!r.ok||!r.audio)return false;          // 超限/未配置/上游失败 → 交回浏览器语音
    cloudStop();
    const a=new Audio(r.audio);
    _cloudAudio=a;_speakingBtn=btn||null;if(btn)btn.classList.add("speaking");
    // 播放出错也当作念完：连读时宁可跳到下一条，不能卡在这条上不动。
    a.onended=a.onerror=()=>{if(_speakingBtn===btn){btn?.classList.remove("speaking");_speakingBtn=null;}if(_cloudAudio===a)_cloudAudio=null;if(done)done();};
    await a.play();
    return true;
  }catch(_){ return false; }
}
function ttsSupported(){return typeof window!=="undefined"&&"speechSynthesis" in window&&"SpeechSynthesisUtterance" in window;}
function ttsRefreshVoices(){
  if(ttsSupported())_ttsVoices=window.speechSynthesis.getVoices()||[];
  const hasZh=_ttsVoices.some(v=>/zh|cmn|中文|chinese/i.test((v.lang||"")+" "+(v.name||"")));
  // 只有「云端不可用」且「拿到语音列表却无中文语音」时才判 no-tts 隐藏朗读按钮。
  // 语音列表为空(尚未加载)不误判；云端可用时本地有没有中文语音都无所谓。
  const localUsable=!ttsSupported()?false:(_ttsVoices.length===0||hasZh);
  document.body.classList.toggle("no-tts",!_cloudTts&&!localUsable);
}
function ttsInit(){
  ttsProbeCloud();   // 不 await：探测失败/慢都不该挡住页面，先按浏览器语音准备着
  if(!ttsSupported()){return;}   // 云端可用时仍能朗读，故这里不再直接判 no-tts
  ttsRefreshVoices();
  window.speechSynthesis.onvoiceschanged=ttsRefreshVoices;  // 多数浏览器异步返回语音
}
let _speakingBtn=null;
// 每次停止/换条都自增。cancel() 会立刻触发上一条的 onend，如果不认这个代号，
// 那次 onend 会被误当成「这条念完了」而连读自动跳下一条 —— 停止键会变成快进键。
let _ttsGen=0;
function ttsStop(){
  _ttsGen++;
  cloudStop();
  if(ttsSupported())window.speechSynthesis.cancel();
  if(_speakingBtn){_speakingBtn.classList.remove("speaking");_speakingBtn=null;}
}
// 暂停/继续：云端是 <audio>，浏览器语音是 speechSynthesis，两套各自的暂停接口。
let _srPauseFallback=false;   // 见下：pause() 无效时改用「掐断 + 续播时重念这条」
function ttsPause(){
  if(_cloudAudio){_cloudAudio.pause();return;}
  if(!ttsSupported())return;
  window.speechSynthesis.pause();
  // 部分安卓浏览器的 pause() 是空操作，按了还在念。老人按暂停必须真的停下来，
  // 那就直接掐断，「继续」时从这一条重念（重念一条远好过暂停键没反应）。
  _srPauseFallback=false;
  setTimeout(()=>{
    if(play.paused&&window.speechSynthesis.speaking&&!window.speechSynthesis.paused){
      _srPauseFallback=true;ttsStop();
    }
  },220);
}
function ttsResume(){
  if(_cloudAudio){_cloudAudio.play().catch(()=>{});return;}
  if(_srPauseFallback){_srPauseFallback=false;if(play.on)playAt(play.i);return;}
  if(ttsSupported())window.speechSynthesis.resume();
}
// 入口：先试云端，云端不接手（未配置/超限/失败）再用浏览器语音。
// done 在这一条念完时回调（连读靠它接下一条），已被停掉的那次不回调。
async function speak(text,btn,done){
  // 再点同一个按钮 = 停止；否则停掉上一条再念新的。连读没有按钮（btn 为空），不参与这个开关。
  const playing=_cloudAudio||(ttsSupported()&&window.speechSynthesis.speaking);
  if(btn&&_speakingBtn===btn&&playing){ttsStop();return;}
  ttsStop();
  const gen=_ttsGen;
  const fin=()=>{if(gen===_ttsGen&&done)done();};
  if(await cloudSpeak(text,btn,fin))return;
  browserSpeak(text,btn,fin);
}
function browserSpeak(text,btn,done){
  if(!ttsSupported()){toast("当前浏览器不支持朗读");return;}
  const u=new SpeechSynthesisUtterance((text||"").replace(/\s+/g," ").trim());
  u.lang="zh-CN";u.rate=0.92;u.pitch=1;
  if(!_ttsVoices.length)ttsRefreshVoices();
  const zh=_ttsVoices.find(v=>/zh|cmn|中文|chinese/i.test((v.lang||"")+" "+(v.name||"")));
  if(zh)u.voice=zh;
  u.onend=()=>{if(_speakingBtn===btn){btn?.classList.remove("speaking");_speakingBtn=null;}if(done)done();};
  u.onerror=u.onend;
  _speakingBtn=btn||null;if(btn)btn.classList.add("speaking");
  window.speechSynthesis.speak(u);
}
// 单条朗读（详情页「朗读全文」、标准版按钮）：先把连读关掉，
// 否则连读的自动跳条会在用户单点的这条上继续往下走。
function speakOne(text,btn){ if(play.on)playStop(true); speak(text,btn); }

// ---- 连续朗读（老人版）----
// 老人不该为了听新闻一条一条去点。这里把当前列表整体当成一条播放队列，从头念到尾，
// 中途可暂停/继续、跳上一条下一条、随时停止。
// 队列存事件 id 而不是列表下标：换筛选、切中英文都会重绘列表，下标会失效，id 不会
// （对应事件不在了就跳过）。
const play={on:false,paused:false,ids:[],i:-1,body:new Map()};
const SR_SUMMARY_CHARS=160;   // 每条摘要念多少字。约 40 秒/条：再长，听的人会忘了这条在说什么

function playIndexOf(id){return state.items.findIndex(c=>c&&c.id===id);}
// 摘要要单独取（/api/feed 只给标题，不带正文），取过就缓存，并提前预取下一条，
// 避免两条之间出现一段莫名的静音。
async function playBody(id){
  if(play.body.has(id))return play.body.get(id);
  let body="";
  try{
    const d=await api(`/api/cluster/${encodeURIComponent(id)}`);
    const lead=(d.items||[]).find(x=>x.source_name===d.headline_src)||(d.items||[])[0];
    const raw=state.disp==="zh"&&(lead?.body_zh||"").trim()?lead.body_zh:(lead?.body||lead?.summary||"");
    body=(raw||"").replace(/\s+/g," ").trim();
    if(body.length>SR_SUMMARY_CHARS){
      const cut=body.slice(0,SR_SUMMARY_CHARS);
      const stop=Math.max(cut.lastIndexOf("。"),cut.lastIndexOf("！"),cut.lastIndexOf("？"));
      body=stop>40?cut.slice(0,stop+1):cut;   // 尽量断在句末，别念半句话
    }
  }catch(_){ /* 取不到摘要就只念标题：宁可短，也不能卡住整个连读 */ }
  play.body.set(id,body);
  return body;
}
// 播报条文案 + 当前卡片高亮。所有状态变化都过这里，避免几处各自刷新出不一致的界面。
function playPaint(){
  document.body.classList.toggle("sr-playing",play.on);
  const bar=$("#sr-player");if(bar)bar.hidden=!play.on;
  const all=$("#sr-play-all");
  if(all){all.textContent=play.on?"⏹ 停止连读":"▶ 连读全部";all.classList.toggle("on",play.on);}
  const t=$("#sr-player-toggle");
  if(t){t.textContent=play.paused?"▶ 继续":"⏸ 暂停";t.title=play.paused?"接着念":"先停一下，位置不丢";}
  const k=play.on&&play.ids[play.i]?playIndexOf(play.ids[play.i]):-1;
  document.querySelectorAll(".ev").forEach(n=>n.classList.toggle("sr-now",k>=0&&+n.dataset.i===k));
  const pos=$("#sr-player-pos");if(pos)pos.textContent=`第 ${play.i+1} / ${play.ids.length} 条`;
  const tit=$("#sr-player-title");if(tit)tit.textContent=k>=0?hl(state.items[k]):"…";
}
function playScroll(){
  const k=play.ids[play.i]?playIndexOf(play.ids[play.i]):-1;
  if(k>=0)document.querySelector(`.ev[data-i="${k}"]`)?.scrollIntoView({behavior:"smooth",block:"center"});
}
async function playAt(k){
  if(!play.on)return;
  if(k<0)k=0;
  if(k>=play.ids.length){playStop(true);toast(`这一页 ${play.ids.length} 条都念完了`);return;}
  play.i=k;play.paused=false;_srPauseFallback=false;
  const id=play.ids[k];
  if(playIndexOf(id)<0){playAt(k+1);return;}   // 列表已刷新、这条不在了 → 跳过
  playPaint();playScroll();
  const body=await playBody(id);
  if(!play.on||play.ids[play.i]!==id)return;   // 等摘要的这段时间里被停掉或跳走了
  const idx=playIndexOf(id);
  if(idx<0){playAt(k+1);return;}
  // 念出编号：听的人看不见高亮时，靠这句知道念到哪了。
  speak(`第${k+1}条。${hl(state.items[idx])}。${body}`,null,
        ()=>{if(play.on&&play.ids[play.i]===id)setTimeout(()=>playAt(k+1),450);});
  if(play.ids[k+1])playBody(play.ids[k+1]);    // 预取下一条摘要
}
function playStart(from){
  if(!state.items.length){toast("当前没有可朗读的新闻");return;}
  if(document.body.classList.contains("no-tts")){toast("这台设备没有可用的中文语音");return;}
  closeDetail();
  play.on=true;play.paused=false;play.ids=state.items.map(c=>c.id);
  playAt(Math.max(0,from||0));
}
// 与 ttsStop 分开：ttsStop 只管当前这句（speak 每次都会先调它），
// playStop 才是整个队列收工。
function playStop(quiet){
  const was=play.on;
  play.on=false;play.paused=false;play.i=-1;_srPauseFallback=false;
  ttsStop();playPaint();
  if(was&&!quiet)toast("已停止朗读");
}
function playToggle(){
  if(!play.on)return;
  play.paused=!play.paused;
  if(play.paused)ttsPause();else ttsResume();
  playPaint();
}

function setMode(mode) {
  state.mode=mode;
  document.querySelectorAll("[data-mode]").forEach(b=>b.classList.toggle("on",b.dataset.mode===mode));
  const personal=mode==="personal";
  $("#mode-banner").innerHTML=personal
    ? `<b>为我推荐</b><span>使用当前画像的相关性门槛；证据分与来源结构不受个人偏好改变。</span>`
    : `<b>公共全景</b><span>相关性门槛设为 0，展示画像之外的事件；仍受当前时间、语言与证据筛选控制。</span>`;
  loadFeed();
}

const commands=[
  {name:"切换到公共全景",keys:"G A",run:()=>setMode("public")},
  {name:"切换到为我推荐",keys:"G P",run:()=>setMode("personal")},
  {name:"视角：综合资讯",keys:"",run:()=>{state.focus="all";initFilters();loadFeed()}},
  {name:"视角：平衡（控制 AI 占比）",keys:"",run:()=>{state.focus="balanced";initFilters();loadFeed()}},
  {name:"视角：AI 专注",keys:"",run:()=>{state.focus="ai";initFilters();loadFeed()}},
  {name:"查看值得看",keys:"1",run:()=>document.querySelector('[data-view="signal"]').click()},
  {name:"查看待证",keys:"2",run:()=>document.querySelector('[data-view="unverified"]').click()},
  {name:"查看噪音",keys:"3",run:()=>document.querySelector('[data-view="noise"]').click()},
  {name:"聚焦搜索标题",keys:"/",run:()=>$("#q").focus()},
  {name:"打开 AI / 科技情报雷达",keys:"I",run:showAIRadar},
  {name:"查看质量门禁与产品边界",keys:"Q",run:showQuality},
  {name:"打开每日简报",keys:"D",run:showDigest},
  {name:"打开资产观察列表",keys:"W",run:showWatchlist},
  {name:"切换老人版 / 标准版",keys:"",run:toggleSenior},
  {name:"显示快捷键",keys:"?",run:help}
];
let commandIndex=0;
function renderCommands() {
  const q=$("#command-q").value.trim().toLowerCase();
  const shown=commands.filter(x=>!q||`${x.name} ${x.keys}`.toLowerCase().includes(q));
  commandIndex=Math.min(commandIndex,Math.max(0,shown.length-1));
  $("#command-list").innerHTML=shown.length?shown.map((x,i)=>`<button class="command-item ${i===commandIndex?"on":""}" data-command="${commands.indexOf(x)}"><span>${esc(x.name)}</span><kbd>${esc(x.keys)}</kbd></button>`).join(""):`<div class="command-empty">没有匹配的命令</div>`;
  document.querySelectorAll("[data-command]").forEach(b=>b.onclick=()=>runCommand(+b.dataset.command));
}
function openCommands() { $("#command-palette").hidden=false; commandIndex=0; $("#command-q").value=""; renderCommands(); setTimeout(()=>$("#command-q").focus(),0); }
function closeCommands() { $("#command-palette").hidden=true; }
function runCommand(i) { const cmd=commands[i]; if(!cmd)return; closeCommands(); cmd.run(); }

async function select(i) {
  if(i<0||i>=state.items.length)return; state.selected=i;
  document.querySelectorAll(".ev").forEach((n,j)=>n.classList.toggle("sel",j===i));
  const c=await api(`/api/cluster/${encodeURIComponent(state.items[i].id)}`), b=c.breakdown||{};
  const ev=c.evidence||{claims:[],contradictions:[],timeline:[]};
  const claimLabels={independently_reported:"多集团分别报道",official_statement:"机构原始声明",single_report:"单一来源报道",disputed:"存在反向证据"};
  const relationLabels={support:"支持",refute:"反驳",unknown:"待判定"};
  const dims=[["信源权威",b.authority],["交叉印证",b.corroboration],["内容质量",b.content],["时间一致",b.timing]];
  const grouped={official:[],media:[],lead:[]};
  (c.items||[]).forEach(x=>grouped[sourceKind(x).key].push(x));
  const composition=[
    ["official","官方 / 机构",grouped.official.length],
    ["media","独立媒体",grouped.media.length],
    ["lead","聚合 / 社区",grouped.lead.length]
  ];
  const unknown=[];
  if(c.n_groups<2) unknown.push("目前只有一个媒体集团，缺少独立交叉报道。");
  if(!grouped.official.length) unknown.push("尚未收录可直接核对的官方文件或机构声明。");
  if(!grouped.media.length) unknown.push("尚无独立媒体报道进入该事件簇。");
  if(grouped.lead.length && !grouped.official.length && !grouped.media.length) unknown.push("当前内容仅构成聚合或社区线索，不应据此下结论。");
  if(c.llm?.verify_next) unknown.push(`建议核查：${c.llm.verify_next}`);
  ev.claims.forEach(x=>(x.unresolved||[]).forEach(note=>{if(!unknown.includes(note))unknown.push(note)}));
  const lead=(c.items||[]).find(x=>x.source_name===c.headline_src)||(c.items||[])[0];
  // 正文预览（items.body，抓取时抽的原文前几段）比 feed 摘要好得多，
  // 但不是每篇都抽得到——抽不到就退回摘要，并在脚注里说清读的是哪一种。
  // 中文模式优先展示正文中文译文（body_zh），没有译文再回退原文正文，再回退摘要。
  const bodyText=state.disp==="zh"&&(lead?.body_zh||"").trim()?lead.body_zh:lead?.body;
  const fromBody=!!(bodyText||"").trim();
  const previewText=((bodyText||lead?.summary)||"").trim();
  const previewParts=previewText.split(/(?:\r?\n){2,}|(?<=[。！？!?])\s+/).map(x=>x.trim()).filter(Boolean).slice(0,4);
  let preview=previewParts.join("\n\n").slice(0,900).trim();
  if(preview.length===900) preview=preview.replace(/[，,；;：:\s][^，,；;：:\s]{0,80}$/u,"").trim()+"…";
  const previewNote=`展示${fromBody?"原文正文":"入库摘要"}的前 ${Math.min(4,previewParts.length)} 段，最多 900 字；请以原始新闻为准。`;
  const renderSource=x=>{const k=sourceKind(x);return `<div class="item source-${k.key}"><span class="source-role">${esc(k.label)}</span><span class="it-t">${time(x.published_ts)}</span><div><a class="source-title-link" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title)} <span aria-hidden="true">↗</span></a><div class="it-s">${esc(x.source_name)} · ${esc(x.grp)} · T${x.tier}</div><div class="source-hint">${esc(k.hint)}</div></div></div>`};
  const senior=isSenior();
  // 朗读用的纯文本：标题 + 正文预览（去掉标记），老人版点「朗读全文」念这段。
  const speakText=`${hl(c)}。${preview?preview.replace(/\s+/g," "):""}`.trim();
  $("#detail").className="detail"; $("#detail").innerHTML=`<button class="detail-back" id="detail-back">&times; 关闭详情 <kbd>Esc</kbd></button><div class="row"><span class="badge b-${c.cred_code}">${credLabel(c.cred_code)}</span><span>${time(c.last_ts)}</span></div>
    <h2>${esc(hl(c))}</h2>${lead?.url?`<a class="original-cta" href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer"><span>阅读原始新闻</span><b>${esc(lead.source_name||c.headline_src)} ↗</b></a>`:""}${senior?`<div class="sr-actions"><button class="sr-btn" id="sr-read-full">🔊 朗读全文</button>${lead?.url?`<a class="sr-btn sr-btn-alt" href="${esc(lead.url)}" target="_blank" rel="noopener noreferrer">📖 读原文</a>`:""}</div>`:""}<div class="bigscore"><b style="color:${color(c.cred_code)}">${c.cred}</b><span>${senior?`可靠程度 / 100 · 报的媒体越多越靠上`:`证据完整度 / 100 · 相关性 ${c.relevance.toFixed(2)}`}</span><b class="doubt-score" style="color:${doubtColor[c.doubt_code||"CLEAR"]}">${Math.round(c.doubt||0)}</b><span>${senior?`可疑程度 / 100 · ${esc(srDoubtFull[c.doubt_code||"CLEAR"])}`:`存疑度 / 100 · ${esc(c.doubt_detail?.label||"无明显存疑")}`}</span></div>${senior?`<div class="score-disclaimer">上面两个数字：左边「可靠程度」是几家媒体在报、证据全不全的综合分；右边「可疑程度」是有多少说不通的地方。两个都不高，才是好消息。</div>`:`<div class="score-disclaimer">左边是证据完整度：来源、交叉报道、内容与时效信号的综合分，不是事件为真的概率。右边是存疑度，只统计主动出现的可疑信号——两个数字回答不同的问题，都不高才是好消息。</div>`}
    <h5>${senior?"有没有可疑":"存疑判定"}</h5><div class="doubt-panel d-${c.doubt_code||"CLEAR"}">${(c.doubt_detail?.reasons||[]).length?(c.doubt_detail.reasons).map(r=>`<div class="doubt-row"><b>+${r.points}</b><span>${esc(r.reason)}</span></div>`).join(""):`<div class="note">${senior?`已经查过了，没发现明显可疑的地方。不过这不代表一定为真，只是没看出问题。`:`已查：${esc((c.doubt_detail?.checked||[]).join("、"))} —— 均未命中可疑信号。这不代表内容为真，只代表没有发现主动的可疑迹象。`}</div>`}</div>
    ${c.entities?.length?`<div class="asset-links">相关实体 / 资产：${c.entities.map(x=>`<button class="chip asset-link" data-asset="${esc(x.symbol||x.entity_id)}" data-kind="${esc(x.kind)}" title="${esc(x.name)} · 命中：${esc(x.matched_terms.join('/'))}">${esc(x.symbol||x.name)}</button>`).join("")}</div>`:""}
    <h5>可追溯断言</h5><div class="claim-list">${ev.claims.length?ev.claims.map(x=>`<div class="claim"><div><span class="claim-status ${x.status}">${claimLabels[x.status]||esc(x.status)}</span> ${esc(x.text)}</div><div class="claim-refs">${x.evidence.map((r,j)=>`<a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.group)}"><span class="claim-relation ${esc(r.relation||"support")}">${relationLabels[r.relation||"support"]}</span>[${j+1}] ${esc(r.source)} ↗</a>`).join("")}</div></div>`).join(""):`<div class="note">尚未提取出可追溯断言</div>`}</div>
    <h5>主要内容</h5>${preview?`<article class="article-preview">${preview.split("\n\n").map(p=>`<p>${esc(p)}</p>`).join("")}<div class="preview-limit">${esc(previewNote)}</div></article>`:`<div class="article-preview empty-preview">该来源未提供可合法展示的摘要或正文段落。请点击上方“阅读原始新闻”查看全文。</div>`}
    ${ev.contradictions.length?`<h5>表述冲突</h5><div class="conflicts">${ev.contradictions.map(x=>`<div class="conflict"><b>⚠ ${esc(x.reason)}</b><div>${esc(x.left.source)}：${esc(x.left.title)}</div><div>${esc(x.right.source)}：${esc(x.right.title)}</div></div>`).join("")}</div>`:""}
    <h5>来源构成</h5><div class="source-composition">${composition.map(([k,n,v])=>`<div class="source-stat ${k}"><b>${v}</b><span>${n}</span></div>`).join("")}</div><div class="bd-note">共 ${c.n_groups} 个独立媒体集团、${c.n_items} 篇内容；同集团多篇不重复计作独立印证。</div>
    <h5>证据为何得到此分</h5><div class="bd">${dims.map(([n,d={score:0}])=>`<div class="bd-row"><div class="bd-top"><span>${n}</span><b>${Math.round(d.score*100)} × ${Math.round((d.weight||0)*100)}%</b></div><div class="bar"><i style="width:${d.score*100}%"></i></div><div class="bd-note">${esc(d.note||"")}</div>${(d.positive||[]).map(x=>`<div class="sig-pos">+ ${esc(x)}</div>`).join("")}${(d.negative||[]).map(x=>`<div class="sig-neg">− ${esc(x)}</div>`).join("")}</div>`).join("")}</div>
    ${c.llm?`<h5>AI 内容甄别</h5><div class="llm"><div class="q">${esc(c.llm.summary||"")}</div><div class="sowhat">${esc(c.llm.so_what||"")}</div>${(c.llm.red_flags||[]).map(x=>`<li class="rf">⚑ ${esc(x)}</li>`).join("")}<div class="note">核实建议：${esc(c.llm.verify_next||"—")}</div></div>`:""}
    <h5>尚待确认 / 未知项</h5><div class="unknowns">${unknown.length?unknown.map(x=>`<div>？ ${esc(x)}</div>`).join(""):`<div>当前没有自动识别出的明显缺口；这不代表信息已经穷尽。</div>`}</div>
    <h5>事件时间线</h5><div class="timeline">${ev.timeline.map(x=>`<div class="timeline-row"><time>${time(x.ts)}</time><span>${esc(x.source)}</span><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></div>`).join("")}</div><div class="note">${esc(ev.limitations||"")}</div>
    <h5>来源链</h5>${[...grouped.official,...grouped.media,...grouped.lead].map(renderSource).join("")}`;
  $("#detail-back").onclick=closeDetail;
  const srFull=$("#sr-read-full");if(srFull)srFull.onclick=()=>speakOne(speakText,srFull);
  document.querySelectorAll("[data-asset]").forEach(b=>b.onclick=()=>{if(b.dataset.kind==="company"){$("#q").value=`asset:${b.dataset.asset}`;loadFeed()}else showMarket(b.dataset.asset)});
}

function modal(title,body,cls="") { const m=document.createElement("div");m.className=`modal ${cls}`.trim();m.innerHTML=`<div class="modal-box"><div class="modal-head"><b>${esc(title)}</b><span class="spacer"></span><button class="btn">关闭 Esc</button></div><div class="modal-body">${body}</div></div>`;m.onclick=e=>{if(e.target===m||e.target.closest(".modal-head .btn"))m.remove()};document.body.append(m);return m; }
function showWelcome(firstVisit=false){const m=modal("为什么有 NEWSDESK",`<section class="welcome-hero"><div class="welcome-kicker">PUBLIC INTELLIGENCE, WITH RECEIPTS</div><h2>只看真新闻。</h2><p>我们并不缺信息，而是被虚假内容、奶头乐，以及伪装成新闻的营销信息淹没。NEWSDESK 的初心，是把注意力还给真正发生、值得理解、能够追溯原始证据的事情。</p><p>这里的“真”不是替你宣布绝对真相，而是明确回答：谁最先说、是否有独立媒体印证、哪些只是机构声明、证据哪里冲突，以及如何回到原始新闻自行核对。</p></section><div class="welcome-difference"><div><b>01</b><strong>来源不混算</strong><span>官方、独立采编、聚合与社区线索分层；同一媒体集团多篇不冒充多源印证。</span></div><div><b>02</b><strong>结论可追溯</strong><span>可信度不是“真假概率”。每条断言绑定来源、原句和支持/反驳关系。</span></div><div><b>03</b><strong>面向中文决策者</strong><span>把中文政策语境与全球 AI、科技、宏观和市场信号放进同一事件流。</span></div></div><h5>第一次使用，只记住三点</h5><div class="onboarding-steps"><button data-tour="news"><b>1</b><span><strong>点开一条新闻</strong>查看来源结构、可信度依据与主要内容。</span></button><button data-tour="topic"><b>2</b><span><strong>左侧选择主题</strong>在科技、经济、政策、民生等领域间切换。</span></button><button data-tour="original"><b>3</b><span><strong>找“阅读原始新闻”</strong>详情顶部的大按钮会直达原始媒体页面。</span></button></div><h5>键盘快捷键（可以先跳过）</h5><div class="keys"><div>命令面板 <kbd>Ctrl K</kbd></div><div>公共全景 / 为我推荐 <kbd>g a / g p</kbd></div><div>值得看 / 待证 / 噪音 <kbd>1 / 2 / 3</kbd></div><div>上下选择事件 <kbd>j / k</kbd></div><div>打开原始新闻 <kbd>o</kbd></div><div>搜索 <kbd>/</kbd></div><div>AI 雷达 <kbd>i</kbd></div><div>质量门禁 <kbd>q</kbd></div><div>观察列表 <kbd>w</kbd></div><div>简报 <kbd>d</kbd></div><div>关闭弹层 / 详情 <kbd>Esc</kbd></div></div><div class="welcome-foot"><span>公开来源 · 本地优先 · 非交易终端</span><button class="btn welcome-start">开始浏览</button></div>`,"welcome-modal");m.querySelector(".welcome-start").onclick=()=>m.remove();m.querySelectorAll("[data-tour]").forEach(b=>b.onclick=()=>{m.remove();const target=b.dataset.tour==="topic"?$("#topics"):b.dataset.tour==="news"?$("#rows"):$("#detail");target?.classList.add("tour-focus");target?.scrollIntoView({behavior:"smooth",block:"center"});setTimeout(()=>target?.classList.remove("tour-focus"),2200)});if(firstVisit)localStorage.setItem("newsdeskWelcomeSeen","1")}
async function showQuality(){const d=await api("/api/quality"),labels={pass:"通过",warn:"待提升",fail:"失败"};modal("质量门禁与产品边界",`<div class="quality-summary"><b>${d.status==="healthy"?"全部通过":d.status==="healthy_with_warnings"?"硬性门禁通过，仍有待提升项":"存在硬性失败"}</b><span>${d.counts.pass} 通过 · ${d.counts.warn} 待提升 · ${d.counts.fail} 失败</span></div><div class="quality-gates">${d.gates.map(g=>`<div class="quality-gate ${g.status}"><span>${labels[g.status]}</span><b>${esc(g.name)}</b><code>${esc(g.value)} / ${esc(g.target)}</code><em>${esc(g.detail||"")}</em></div>`).join("")}</div><h5>明确边界</h5>${d.boundaries.map(x=>`<div class="note">• ${esc(x)}</div>`).join("")}`)}
async function showAIRadar(){const d=await api("/api/ai-radar?hours=72"),evLabel={independent:"独立印证",primary:"一次发布",single:"单源报道"};modal("AI / 科技情报雷达",`<div class="radar-summary"><div><b>${d.clusters}</b><span>72H AI事件</span></div><div><b>${d.official_clusters}</b><span>一次信源</span></div><div><b>${d.reporting_clusters}</b><span>媒体跟进</span></div><div><b>${d.independently_corroborated}</b><span>独立印证</span></div></div><div class="note">${d.sources.enabled} 个 AI 专线信源：${d.sources.official} 个实验室/研究一次源，${d.sources.reporting} 个独立采编源。事件按“独立印证 → 一次发布 → 单源报道”排序；预印本和厂商公告不会自动视为独立确认。</div><h5>情报赛道</h5><div class="radar-topics">${Object.entries(d.topic_counts).map(([k,v])=>`<button class="chip" data-radar-topic="${esc(k)}">${esc(topicLabels[k]||k)} <em>${v}</em></button>`).join("")||"暂无事件"}</div><h5>高频实体</h5><div class="radar-entities">${d.top_entities.map(x=>`<button class="chip" data-radar-asset="${esc(x.symbol||x.id)}">${esc(x.symbol||x.name)} <em>${x.n}</em></button>`).join("")||"数据正在积累"}</div><h5>重要事件</h5><div class="radar-events">${d.items.slice(0,15).map(x=>`<button data-radar-headline="${esc(x.headline)}"><span>${esc(evLabel[x.evidence_status]||x.evidence_status)} · ${esc(x.headline_src)} · ${Math.round(x.cred)}分</span><b>${esc(x.headline)}</b><em>${x.topics.filter(t=>t.startsWith("ai_")).map(t=>topicLabels[t]||t).join(" / ")}</em></button>`).join("")||'<div class="empty">刷新后将展示 AI 专线事件</div>'}</div>`);setTimeout(()=>{document.querySelectorAll("[data-radar-topic]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();state.topic=b.dataset.radarTopic;loadFeed()});document.querySelectorAll("[data-radar-asset]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`asset:${b.dataset.radarAsset}`;loadFeed()});document.querySelectorAll("[data-radar-headline]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`"${b.dataset.radarHeadline}"`;loadFeed()})},0)}
const DG_KIND={summary:"发生了什么",so_what:"对你意味着",red:"⚑ 可疑点",verify:"自己核实",pos:"正向信号",neg:"风险信号"};
function dgEvent(c){const meta=[`${c.n_groups} 个独立信源`,`${c.n_items} 篇报道`,esc(c.src),c.ts].map(x=>`<span>${x}</span>`).join("");
  const pts=(c.points||[]).map(p=>`<div class="dg-pt dg-${p.kind}"><b>${esc(DG_KIND[p.kind]||p.label)}</b><span>${esc(p.text)}</span></div>`).join("");
  return `<article class="dg-card"><div class="dg-rail" style="background:${color(c.cred_code)}"></div><div class="dg-main">
    <div class="dg-head">${c.rank?`<span class="dg-rank">${c.rank}</span>`:""}<span class="badge b-${c.cred_code}">${labels[c.cred_code]}</span><span class="dg-score">${c.cred} 分 · ${esc(c.cred_label)}</span></div>
    <div class="dg-title">${esc(hl(c))}</div>
    <div class="dg-meta">${meta}</div>${pts?`<div class="dg-pts">${pts}</div>`:""}
    ${c.url?`<a class="dg-orig" href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">阅读原始新闻 ↗</a>`:""}</div></article>`;}
async function showDigest(){
  const d=await api("/api/digest?format=json");
  const sec=(n,title,sub,inner)=>`<section class="dg-sec"><h4><span class="dg-n">${n}</span>${title}${sub?`<em>${esc(sub)}</em>`:""}</h4>${inner}</section>`;
  const signal=d.signal.length?d.signal.map(dgEvent).join(""):`<div class="note">本窗口内没有同时满足可信度与相关性门槛的事件。</div>`;
  const unv=d.unverified.length?d.unverified.map(dgEvent).join(""):`<div class="note">无。</div>`;
  const noise=d.noise.length?`<div class="dg-noise">${d.noise.map(n=>`<div>🚫 <s>${esc(hl(n))}</s> <em>${esc(n.why)}</em></div>`).join("")}</div>`:`<div class="note">无。</div>`;
  const okN=d.health.filter(h=>h.ok).length,totN=d.health.length;
  const hero=`<div class="dg-hero"><div class="dg-hero-top"><span class="dg-win">近 ${d.window_hours} 小时 · 为你精选</span><span class="dg-profile">${esc(d.profile||"")}</span></div>
    <div class="dg-stats"><div class="dg-stat"><b>${d.counts.events}</b><span>抓取事件</span></div><div class="dg-stat ok"><b>${d.counts.signal}</b><span>值得看</span></div><div class="dg-stat mute"><b>${d.counts.noise}</b><span>过滤噪音</span></div><div class="dg-stat"><b>${okN}/${totN}</b><span>信源在线</span></div></div></div>`;
  const body=hero
    +sec("①","值得你看的","可信度 × 相关性 排序",signal)
    +sec("②","单源待证","只有一家在说，别急着当事实",unv)
    +sec("③","被过滤的噪音","抽样",noise);
  modal(`每日简报 · ${esc(d.generated_at)}`,body,"digest-modal");}
function sparkline(points){if(points.length<2)return '<div class="note">历史数据正在积累；至少需要两个快照。</div>';const vals=points.map(x=>x.price),lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||1,path=vals.map((v,i)=>`${i?"L":"M"}${(i/(vals.length-1)*520).toFixed(1)},${(90-(v-lo)/span*75).toFixed(1)}`).join(" ");return `<svg class="spark" viewBox="0 0 520 100" preserveAspectRatio="none"><path d="${path}"/></svg><div class="chart-range">${Number(lo).toFixed(4)} — ${Number(hi).toFixed(4)} · ${points.length} 个快照</div>`}
async function showMarket(symbol){const [m,h,w]=await Promise.all([api("/api/markets"),api(`/api/markets/history/${encodeURIComponent(symbol)}?hours=168`),api("/api/watchlist")]);const x=m.instruments.find(v=>v.symbol===symbol),watched=w.items.some(v=>v.symbol===symbol);if(!x)return toast("资产暂不可用");modal(`${x.name} · ${symbol}`,`<div class="market-detail"><div class="bigscore"><b>${Number(x.price).toLocaleString("zh-CN",{maximumFractionDigits:4})}</b><span>${esc(x.currency)} · ${esc(x.asset)} · 延迟数据</span></div>${sparkline(h.points)}<div class="note">来源：${esc(x.source)}。仅供新闻背景参考，不可用于交易执行。</div><button class="btn" id="toggle-watch">${watched?"移出观察列表":"加入观察列表"}</button><button class="btn" id="asset-news">查看相关新闻</button></div>`);setTimeout(()=>{$("#toggle-watch").onclick=async()=>{try{await api(`/api/watchlist${watched?`/${encodeURIComponent(symbol)}`:""}`,watched?{method:"DELETE"}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol})});document.querySelector(".modal")?.remove();showMarket(symbol)}catch(e){toast(String(e.message).startsWith("401")?"观察列表由管理员在管理后台维护":`操作失败：${e.message}`)}};$("#asset-news").onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`asset:${symbol}`;loadFeed()}},0)}
async function showWatchlist(){const d=await api("/api/watchlist");modal("资产观察列表",d.items.length?`<div class="watch-grid">${d.items.map(x=>x.market?`<button class="watch-card" data-watch="${esc(x.symbol)}"><b>${esc(x.market.name)}</b><strong>${Number(x.market.price).toLocaleString("zh-CN",{maximumFractionDigits:4})}</strong><span>${esc(x.symbol)} · ${esc(x.market.asset)}</span></button>`:`<div class="watch-card"><b>${esc(x.symbol)}</b><span>当前无报价</span></div>`).join("")}</div>`:`<div class="empty">尚未添加资产；点击顶部行情即可加入。</div>`);setTimeout(()=>document.querySelectorAll("[data-watch]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();showMarket(b.dataset.watch)}),0)}
function help(){showWelcome(false)}

document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{state.view=b.dataset.view;document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x===b));loadFeed()});
$("#mc").oninput=e=>{$("#mc-val").textContent=e.target.value;state.minCred=+e.target.value;loadFeed()};
let qt;$("#q").oninput=()=>{clearTimeout(qt);qt=setTimeout(loadFeed,250)};
$("#btn-about").onclick=()=>showWelcome(false);$("#btn-ai-radar").onclick=showAIRadar;$("#btn-digest").onclick=showDigest;$("#btn-help").onclick=help;$("#btn-command").onclick=openCommands;$("#btn-lang").onclick=toggleDisp;const _bs=$("#btn-senior");if(_bs)_bs.onclick=toggleSenior;
// 老人版工具条：连读 + 字号三档 + 一键回标准版。老人版里顶栏那排小按钮不好点，控制项收在这。
$("#sr-font-dn").onclick=()=>bumpSrSize(-1);$("#sr-font-up").onclick=()=>bumpSrSize(1);$("#sr-exit").onclick=toggleSenior;
$("#sr-play-all").onclick=()=>{if(play.on)playStop();else playStart(0);};
$("#sr-player-toggle").onclick=playToggle;
$("#sr-player-next").onclick=()=>playAt(play.i+1);
$("#sr-player-prev").onclick=()=>playAt(play.i-1);
$("#sr-player-stop").onclick=()=>playStop();
document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$("#command-palette").onclick=e=>{if(e.target===$("#command-palette"))closeCommands()};
$("#command-q").oninput=()=>{commandIndex=0;renderCommands()};
$("#command-q").onkeydown=e=>{const shown=[...document.querySelectorAll("[data-command]")];if(e.key==="ArrowDown"){e.preventDefault();commandIndex=Math.min(shown.length-1,commandIndex+1);renderCommands()}if(e.key==="ArrowUp"){e.preventDefault();commandIndex=Math.max(0,commandIndex-1);renderCommands()}if(e.key==="Enter"){e.preventDefault();shown[commandIndex]?.click()}if(e.key==="Escape"){e.preventDefault();closeCommands()}};
let gChord=0;
document.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();$("#command-palette").hidden?openCommands():closeCommands();return}if(!$("#command-palette").hidden)return;if(e.key==="Escape"){document.querySelector(".modal")?.remove();closeDetail();return}if(["INPUT","TEXTAREA"].includes(document.activeElement.tagName)){if(e.key==="Escape")document.activeElement.blur();return}const key=e.key.toLowerCase();if(key==="g"){gChord=Date.now();return}if(Date.now()-gChord<900&&(key==="a"||key==="p")){setMode(key==="a"?"public":"personal");gChord=0;return}gChord=0;if("123".includes(e.key))document.querySelectorAll(".tab")[+e.key-1]?.click();if(e.key==="/"){e.preventDefault();$("#q").focus()}if(e.key==="j")select(Math.min(state.items.length-1,state.selected+1));if(e.key==="k")select(Math.max(0,state.selected-1));if(e.key==="o"&&state.selected>=0)window.open(state.items[state.selected].url,"_blank","noopener");if(e.key==="w")showWatchlist();if(e.key==="q")showQuality();if(e.key==="i")showAIRadar();if(e.key==="d")showDigest()};
setInterval(()=>$("#clock").textContent=new Date().toLocaleTimeString("zh-CN",{hour12:false}),1000);
initFilters(); syncDispBtn(); applySenior(); ttsInit(); setMode("personal"); loadStats().catch(e=>toast(`加载失败：${e.message}`)); loadMarkets();
// 首次访问：先弹「为什么有 NEWSDESK」，关掉后再轻问一句要不要老人版（避免两个弹窗叠加）；
// 老访客直接问一次（askSenior 自带 flag，问过一次不再打扰）。
if(!localStorage.getItem("newsdeskWelcomeSeen")){const _wm=showWelcome(true);const _obs=new MutationObserver(()=>{if(!document.body.contains(_wm)){_obs.disconnect();setTimeout(askSenior,450);}});_obs.observe(document.body,{childList:true});}else{setTimeout(askSenior,450);}
setInterval(loadMarkets,120000);
