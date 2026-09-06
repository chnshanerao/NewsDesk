"use strict";

const $ = (s) => document.querySelector(s);
const state = {view: "signal", mode: "personal", topic: "all", lang: "all", hours: 48, order: "rank", minCred: 0,
               items: [], selected: -1, profile: null};
const labels = {CONFIRMED: "✅ 多集团报道", LIKELY: "🟢 证据较完整",
                SINGLE: "🟡 单源待证", LOW: "🔴 证据较弱"};
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

function esc(v) { const d=document.createElement("div"); d.textContent=v??""; return d.innerHTML; }
function time(ts) { return ts ? new Date(ts*1000).toLocaleString("zh-CN", {hour12:false, month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—"; }
async function api(url,opt={},retried=false){const method=(opt.method||"GET").toUpperCase(),headers=new Headers(opt.headers||{}),token=sessionStorage.getItem("newsdeskWriteToken");if(method!=="GET"&&token)headers.set("X-Newsdesk-Token",token);const r=await fetch(url,{...opt,headers});if(r.status===401&&method!=="GET"&&!retried){const entered=prompt("请输入 NEWSDESK 管理令牌（服务器 data/admin-token）");if(entered){sessionStorage.setItem("newsdeskWriteToken",entered.trim());return api(url,opt,true)}}if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return r.json()}
function toast(msg) { const n=document.createElement("div"); n.className="toast"; n.textContent=msg; document.body.append(n); setTimeout(()=>n.remove(),2600); }
window.addEventListener("unhandledrejection",event=>{const message=event.reason?.message||String(event.reason||"未知错误");toast(`操作失败：${message}`);event.preventDefault()});
function color(code) { return ({CONFIRMED:"var(--good)",LIKELY:"var(--warn)",SINGLE:"var(--serious)",LOW:"var(--critical)"})[code]; }

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
  document.querySelectorAll("[data-topic]").forEach(b=>b.onclick=()=>{state.topic=b.dataset.topic; loadFeed();});
}

function initFilters() {
  $("#hours").innerHTML=[[24,"24 小时"],[48,"48 小时"],[168,"7 天"]].map(([v,l])=>`<button class="chip ${state.hours===v?"on":""}" data-hours="${v}">${l}</button>`).join("");
  $("#languages").innerHTML=[["all","多语言混排"],["zh","仅中文"],["en","仅 English"],["pt","Português"]].map(([v,l])=>`<button class="chip ${state.lang===v?"on":""}" data-lang="${v}">${l}</button>`).join("");
  $("#orders").innerHTML=[["rank","综合价值"],["time","最新发布"]].map(([v,l])=>`<button class="chip ${state.order===v?"on":""}" data-order="${v}">${l}</button>`).join("");
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
  const p=new URLSearchParams({view:state.view,hours:state.hours,lang:state.lang,order:state.order,min_cred:state.minCred,limit:120});
  // 公共全景显式解除画像相关性门槛；个人模式让服务端采用当前画像门槛。
  if(state.mode==="public") p.set("min_rel","0");
  if(state.topic!=="all") p.set("topic",state.topic); if($("#q").value.trim()) p.set("q",$("#q").value.trim());
  const d=await api("/api/feed?"+p); state.items=d.items; state.selected=-1;
  renderParsedQuery(d.parsed_query||{});
  const searched=$("#q").value.trim()?" · 检索结果":"";
  $("#count").textContent=`${d.total} 个事件 · ${state.view==="signal"?"值得看":state.view==="unverified"?"待证":"噪音"}${searched}`;
  $("#rows").innerHTML=d.items.length?d.items.map((c,i)=>`<article class="ev" data-i="${i}" tabindex="0">
    <div class="ts">${time(c.last_ts)}</div><div class="cr" style="color:${color(c.cred_code)}">${Math.round(c.cred)}</div>
    <div class="body"><div class="hl">${esc(c.headline)}</div><div class="meta"><span class="badge b-${c.cred_code}">${labels[c.cred_code]}</span><span class="tier t${c.best_tier}">T${c.best_tier}</span><span class="src">${esc(c.headline_src)}</span><span class="gcount">${c.n_groups}源/${c.n_items}篇</span>${(c.languages||[]).map(x=>`<span class="tier">${esc(x.toUpperCase())}</span>`).join("")}${c.topics.map(t=>`<span class="topic">#${esc(topicLabels[t]||t)}</span>`).join("")}${c.llm?.red_flags?.length?`<span class="flag">⚑${c.llm.red_flags.length}</span>`:""}</div><div class="meter"><i style="width:${c.cred}%;background:${color(c.cred_code)}"></i></div></div></article>`).join(""):`<div class="note" style="padding:30px;text-align:center">当前筛选条件下没有事件</div>`;
  document.querySelectorAll(".ev").forEach(n=>n.onclick=()=>select(+n.dataset.i));
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
  {name:"查看值得看",keys:"1",run:()=>document.querySelector('[data-view="signal"]').click()},
  {name:"查看待证",keys:"2",run:()=>document.querySelector('[data-view="unverified"]').click()},
  {name:"查看噪音",keys:"3",run:()=>document.querySelector('[data-view="noise"]').click()},
  {name:"聚焦搜索标题",keys:"/",run:()=>$("#q").focus()},
  {name:"刷新全部信源",keys:"R",run:refresh},
  {name:"打开信源注册表",keys:"S",run:showSources},
  {name:"打开 AI / 科技情报雷达",keys:"I",run:showAIRadar},
  {name:"打开引用研究工作台",keys:"E",run:showResearch},
  {name:"查看质量门禁",keys:"Q",run:showQuality},
  {name:"打开每日简报",keys:"D",run:showDigest},
  {name:"打开监控规则",keys:"A",run:showAlerts},
  {name:"打开资产观察列表",keys:"W",run:showWatchlist},
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
  const renderSource=x=>{const k=sourceKind(x);return `<div class="item source-${k.key}"><span class="source-role">${esc(k.label)}</span><span class="it-t">${time(x.published_ts)}</span><div><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a><div class="it-s">${esc(x.source_name)} · ${esc(x.grp)} · T${x.tier}</div><div class="source-hint">${esc(k.hint)}</div></div></div>`};
  $("#detail").className="detail"; $("#detail").innerHTML=`<div class="row"><span class="badge b-${c.cred_code}">${labels[c.cred_code]}</span><span>${time(c.last_ts)}</span></div>
    <h2>${esc(c.headline)}</h2><div class="bigscore"><b style="color:${color(c.cred_code)}">${c.cred}</b><span>证据完整度 / 100 · 相关性 ${c.relevance.toFixed(2)}</span></div><div class="score-disclaimer">这是来源、交叉报道、内容与时效信号的综合分，不是事件为真的概率。</div>
    ${c.entities?.length?`<div class="asset-links">相关实体 / 资产：${c.entities.map(x=>`<button class="chip asset-link" data-asset="${esc(x.symbol||x.entity_id)}" data-kind="${esc(x.kind)}" title="${esc(x.name)} · 命中：${esc(x.matched_terms.join('/'))}">${esc(x.symbol||x.name)}</button>`).join("")}</div>`:""}
    <h5>可追溯断言</h5><div class="claim-list">${ev.claims.length?ev.claims.map(x=>`<div class="claim"><div><span class="claim-status ${x.status}">${claimLabels[x.status]||esc(x.status)}</span> ${esc(x.text)}</div><div class="claim-refs">${x.evidence.map((r,j)=>`<a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.group)}"><span class="claim-relation ${esc(r.relation||"support")}">${relationLabels[r.relation||"support"]}</span>[${j+1}] ${esc(r.source)}</a>`).join(" · ")}</div></div>`).join(""):`<div class="note">尚未提取出可追溯断言</div>`}</div>
    ${ev.contradictions.length?`<h5>表述冲突</h5><div class="conflicts">${ev.contradictions.map(x=>`<div class="conflict"><b>⚠ ${esc(x.reason)}</b><div>${esc(x.left.source)}：${esc(x.left.title)}</div><div>${esc(x.right.source)}：${esc(x.right.title)}</div></div>`).join("")}</div>`:""}
    <h5>来源构成</h5><div class="source-composition">${composition.map(([k,n,v])=>`<div class="source-stat ${k}"><b>${v}</b><span>${n}</span></div>`).join("")}</div><div class="bd-note">共 ${c.n_groups} 个独立媒体集团、${c.n_items} 篇内容；同集团多篇不重复计作独立印证。</div>
    <h5>证据为何得到此分</h5><div class="bd">${dims.map(([n,d={score:0}])=>`<div class="bd-row"><div class="bd-top"><span>${n}</span><b>${Math.round(d.score*100)} × ${Math.round((d.weight||0)*100)}%</b></div><div class="bar"><i style="width:${d.score*100}%"></i></div><div class="bd-note">${esc(d.note||"")}</div>${(d.positive||[]).map(x=>`<div class="sig-pos">+ ${esc(x)}</div>`).join("")}${(d.negative||[]).map(x=>`<div class="sig-neg">− ${esc(x)}</div>`).join("")}</div>`).join("")}</div>
    ${c.llm?`<h5>AI 内容甄别</h5><div class="llm"><div class="q">${esc(c.llm.summary||"")}</div><div class="sowhat">${esc(c.llm.so_what||"")}</div>${(c.llm.red_flags||[]).map(x=>`<li class="rf">⚑ ${esc(x)}</li>`).join("")}<div class="note">核实建议：${esc(c.llm.verify_next||"—")}</div></div>`:""}
    <h5>尚待确认 / 未知项</h5><div class="unknowns">${unknown.length?unknown.map(x=>`<div>？ ${esc(x)}</div>`).join(""):`<div>当前没有自动识别出的明显缺口；这不代表信息已经穷尽。</div>`}</div>
    <h5>事件时间线</h5><div class="timeline">${ev.timeline.map(x=>`<div class="timeline-row"><time>${time(x.ts)}</time><span>${esc(x.source)}</span><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></div>`).join("")}</div><div class="note">${esc(ev.limitations||"")}</div>
    <h5>来源链</h5>${[...grouped.official,...grouped.media,...grouped.lead].map(renderSource).join("")}`;
  document.querySelectorAll("[data-asset]").forEach(b=>b.onclick=()=>{if(b.dataset.kind==="company"){$("#q").value=`asset:${b.dataset.asset}`;loadFeed()}else showMarket(b.dataset.asset)});
}

function modal(title,body) { const m=document.createElement("div");m.className="modal";m.innerHTML=`<div class="modal-box"><div class="modal-head"><b>${esc(title)}</b><span class="spacer"></span><button class="btn">关闭 Esc</button></div><div class="modal-body">${body}</div></div>`;m.onclick=e=>{if(e.target===m||e.target.closest(".modal-head .btn"))m.remove()};document.body.append(m); }
async function showSources(){const d=await api("/api/sources"),vl={healthy:"健康",degraded:"降级",unhealthy:"解析异常",down:"不可达",disabled:"停用",unseen:"未检查"};modal("信源健康中心",`<table><thead><tr><th>裁决</th><th>传输/解析/时效</th><th>层级</th><th>语言/地区</th><th>信源</th><th>角色</th><th>条数</th><th>说明</th></tr></thead><tbody>${d.sources.map(s=>`<tr class="${s.enabled?"":"off"}"><td class="${s.ok?"ok":"err"}">${vl[s.verdict]||esc(s.verdict)}</td><td>${esc(s.transport_status)} / ${esc(s.parse_status)} / ${esc(s.freshness_status)}</td><td>T${s.tier}</td><td>${esc(s.lang.toUpperCase())} · ${esc(s.region)}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></td><td>${esc(s.source_role)}</td><td>${s.last_items}</td><td class="note">${esc(s.last_error||s.note||"")}</td></tr>`).join("")}</tbody></table>`)}
async function showQuality(){const d=await api("/api/quality"),labels={pass:"通过",warn:"待提升",fail:"失败"};modal("质量门禁与产品边界",`<div class="quality-summary"><b>${d.status==="healthy"?"全部通过":d.status==="healthy_with_warnings"?"硬性门禁通过，仍有待提升项":"存在硬性失败"}</b><span>${d.counts.pass} 通过 · ${d.counts.warn} 待提升 · ${d.counts.fail} 失败</span></div><div class="quality-gates">${d.gates.map(g=>`<div class="quality-gate ${g.status}"><span>${labels[g.status]}</span><b>${esc(g.name)}</b><code>${esc(g.value)} / ${esc(g.target)}</code><em>${esc(g.detail||"")}</em></div>`).join("")}</div><h5>明确边界</h5>${d.boundaries.map(x=>`<div class="note">• ${esc(x)}</div>`).join("")}`)}
async function showAIRadar(){const d=await api("/api/ai-radar?hours=72"),evLabel={independent:"独立印证",primary:"一次发布",single:"单源报道"};modal("AI / 科技情报雷达",`<div class="radar-summary"><div><b>${d.clusters}</b><span>72H AI事件</span></div><div><b>${d.official_clusters}</b><span>一次信源</span></div><div><b>${d.reporting_clusters}</b><span>媒体跟进</span></div><div><b>${d.independently_corroborated}</b><span>独立印证</span></div></div><div class="note">${d.sources.enabled} 个 AI 专线信源：${d.sources.official} 个实验室/研究一次源，${d.sources.reporting} 个独立采编源。事件按“独立印证 → 一次发布 → 单源报道”排序；预印本和厂商公告不会自动视为独立确认。</div><h5>情报赛道</h5><div class="radar-topics">${Object.entries(d.topic_counts).map(([k,v])=>`<button class="chip" data-radar-topic="${esc(k)}">${esc(topicLabels[k]||k)} <em>${v}</em></button>`).join("")||"暂无事件"}</div><h5>高频实体</h5><div class="radar-entities">${d.top_entities.map(x=>`<button class="chip" data-radar-asset="${esc(x.symbol||x.id)}">${esc(x.symbol||x.name)} <em>${x.n}</em></button>`).join("")||"数据正在积累"}</div><h5>重要事件</h5><div class="radar-events">${d.items.slice(0,15).map(x=>`<button data-radar-headline="${esc(x.headline)}"><span>${esc(evLabel[x.evidence_status]||x.evidence_status)} · ${esc(x.headline_src)} · ${Math.round(x.cred)}分</span><b>${esc(x.headline)}</b><em>${x.topics.filter(t=>t.startsWith("ai_")).map(t=>topicLabels[t]||t).join(" / ")}</em></button>`).join("")||'<div class="empty">刷新后将展示 AI 专线事件</div>'}</div>`);setTimeout(()=>{document.querySelectorAll("[data-radar-topic]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();state.topic=b.dataset.radarTopic;loadFeed()});document.querySelectorAll("[data-radar-asset]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`asset:${b.dataset.radarAsset}`;loadFeed()});document.querySelectorAll("[data-radar-headline]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`"${b.dataset.radarHeadline}"`;loadFeed()})},0)}
function researchForm(){return '<div class="research-search"><input id="research-q" class="search" placeholder="例如：过去一周 AI 芯片有哪些重要进展？"><button class="btn" id="research-run">检索证据</button></div><div class="note">当前为抽取式研究：每条事实必须绑定原始标题或摘要原句，不让模型补写无引用结论。</div><div id="research-results"></div>'}
async function runResearch(){const q=$("#research-q").value.trim(),box=$("#research-results");if(q.length<2){box.innerHTML='<div class="note">请输入至少两个字符。</div>';return}box.innerHTML='<div class="note">正在检索 claim 证据库…</div>';const d=await api("/api/research?q="+encodeURIComponent(q)+"&limit=10"),labels={independently_reported:"独立印证",official_statement:"机构声明",single_report:"单源待证",disputed:"存在反向证据"},rels={support:"支持",refute:"反驳",unknown:"待判定"};if(!d.findings.length){box.innerHTML='<div class="empty">没有找到足够接近且带原始引文的结论。请换用公司、模型或技术关键词。</div>';return}box.innerHTML='<div class="research-summary">'+d.findings.length+' 条可引用结论 · 引用覆盖率 '+Math.round(d.citation_coverage*100)+'%</div>'+d.findings.map((x,i)=>'<article class="research-finding"><div class="research-number">'+String(i+1).padStart(2,"0")+'</div><div><span class="claim-status '+esc(x.status)+'">'+esc(labels[x.status]||x.status)+'</span><p>'+esc(x.sentence)+'</p><div class="research-cites">'+x.citations.map((c,j)=>'<a href="'+esc(c.url)+'" target="_blank" rel="noopener noreferrer"><b><span class="claim-relation '+esc(c.relation||"support")+'">'+esc(rels[c.relation||"support"])+'</span>['+(j+1)+'] '+esc(c.source)+'</b><q>'+esc(c.quote)+'</q><small>'+esc(c.quote_field)+' '+c.quote_start+'–'+c.quote_end+' · '+esc(c.quote_hash)+'</small></a>').join("")+'</div></div></article>').join("")+'<div class="research-limits">'+d.limitations.map(x=>'<div>• '+esc(x)+'</div>').join("")+'</div>'}
function showResearch(){modal("引用研究工作台",researchForm());setTimeout(()=>{$("#research-run").onclick=runResearch;$("#research-q").onkeydown=e=>{if(e.key==="Enter")runResearch()};$("#research-q").focus()},0)}
async function showDigest(){const r=await fetch("/api/digest");modal("每日简报",`<pre>${esc(await r.text())}</pre>`)}
async function loadAlertBadge(){try{const d=await api("/api/alert-events?unread=1&limit=100");const b=$("#alert-badge");b.textContent=d.unread;b.hidden=!d.unread}catch(_){}}
async function showAlerts(){const [d,ev]=await Promise.all([api("/api/alerts"),api("/api/alert-events?limit=30")]);modal("事件监控",`<div class="alert-create"><input id="alert-name" class="search" placeholder="规则名称"><button class="btn" id="save-alert">保存当前筛选</button></div><div class="note">规则按当前搜索词、主题、语言和证据门槛捕获刷新后出现的新事件。</div>${ev.events.length?`<h5>最近命中 <button class="btn" id="alerts-read">全部已读</button></h5><div class="alert-events">${ev.events.map(x=>`<button class="alert-event ${x.read_ts?"read":""}" data-headline="${esc(x.headline)}"><span>${esc(x.alert_name)}</span><b>${esc(x.headline)}</b><em>${Math.round(x.cred)}分 · ${time(x.event_ts)}</em></button>`).join("")}</div>`:""}<h5>监控规则</h5><div class="alert-list">${d.alerts.length?d.alerts.map(a=>`<div class="alert-row"><div><b>${esc(a.name)}</b><span>${esc(a.q||"全部关键词")} · ${esc(a.topic)} · ${esc(a.lang)} · ≥${a.min_cred}</span></div><strong>${a.match_count_24h}</strong><button class="btn alert-delete" data-id="${a.id}">删除</button></div>`).join(""):`<div class="empty">尚无监控规则</div>`}</div>`);setTimeout(()=>{$("#save-alert").onclick=async()=>{const name=$("#alert-name").value.trim();if(!name)return toast("请输入规则名称");await api("/api/alerts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,q:$("#q").value.trim(),topic:state.topic,lang:state.lang,min_cred:state.minCred})});document.querySelector(".modal")?.remove();showAlerts()};$("#alerts-read")&&($("#alerts-read").onclick=async()=>{await api("/api/alert-events/read",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});document.querySelector(".modal")?.remove();loadAlertBadge();showAlerts()});document.querySelectorAll(".alert-event").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=b.dataset.headline;loadFeed()});document.querySelectorAll(".alert-delete").forEach(b=>b.onclick=async()=>{await api(`/api/alerts/${b.dataset.id}`,{method:"DELETE"});document.querySelector(".modal")?.remove();showAlerts()})},0)}
function sparkline(points){if(points.length<2)return '<div class="note">历史数据正在积累；至少需要两个快照。</div>';const vals=points.map(x=>x.price),lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||1,path=vals.map((v,i)=>`${i?"L":"M"}${(i/(vals.length-1)*520).toFixed(1)},${(90-(v-lo)/span*75).toFixed(1)}`).join(" ");return `<svg class="spark" viewBox="0 0 520 100" preserveAspectRatio="none"><path d="${path}"/></svg><div class="chart-range">${Number(lo).toFixed(4)} — ${Number(hi).toFixed(4)} · ${points.length} 个快照</div>`}
async function showMarket(symbol){const [m,h,w]=await Promise.all([api("/api/markets"),api(`/api/markets/history/${encodeURIComponent(symbol)}?hours=168`),api("/api/watchlist")]);const x=m.instruments.find(v=>v.symbol===symbol),watched=w.items.some(v=>v.symbol===symbol);if(!x)return toast("资产暂不可用");modal(`${x.name} · ${symbol}`,`<div class="market-detail"><div class="bigscore"><b>${Number(x.price).toLocaleString("zh-CN",{maximumFractionDigits:4})}</b><span>${esc(x.currency)} · ${esc(x.asset)} · 延迟数据</span></div>${sparkline(h.points)}<div class="note">来源：${esc(x.source)}。仅供新闻背景参考，不可用于交易执行。</div><button class="btn" id="toggle-watch">${watched?"移出观察列表":"加入观察列表"}</button><button class="btn" id="asset-news">查看相关新闻</button></div>`);setTimeout(()=>{$("#toggle-watch").onclick=async()=>{await api(`/api/watchlist${watched?`/${encodeURIComponent(symbol)}`:""}`,watched?{method:"DELETE"}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol})});document.querySelector(".modal")?.remove();showMarket(symbol)};$("#asset-news").onclick=()=>{document.querySelector(".modal")?.remove();$("#q").value=`asset:${symbol}`;loadFeed()}},0)}
async function showWatchlist(){const d=await api("/api/watchlist");modal("资产观察列表",d.items.length?`<div class="watch-grid">${d.items.map(x=>x.market?`<button class="watch-card" data-watch="${esc(x.symbol)}"><b>${esc(x.market.name)}</b><strong>${Number(x.market.price).toLocaleString("zh-CN",{maximumFractionDigits:4})}</strong><span>${esc(x.symbol)} · ${esc(x.market.asset)}</span></button>`:`<div class="watch-card"><b>${esc(x.symbol)}</b><span>当前无报价</span></div>`).join("")}</div>`:`<div class="empty">尚未添加资产；点击顶部行情即可加入。</div>`);setTimeout(()=>document.querySelectorAll("[data-watch]").forEach(b=>b.onclick=()=>{document.querySelector(".modal")?.remove();showMarket(b.dataset.watch)}),0)}
function help(){modal("快捷键",`<div class="keys"><div>打开命令面板 <kbd>Ctrl K</kbd></div><div>公共全景 / 为我推荐 <kbd>g a / g p</kbd></div><div>切换值得看/待证/噪音 <kbd>1/2/3</kbd></div><div>上下选择事件 <kbd>j / k</kbd></div><div>打开原文 <kbd>o</kbd></div><div>搜索 <kbd>/</kbd></div><div>AI / 科技雷达 <kbd>i</kbd></div><div>监控规则 <kbd>a</kbd></div><div>观察列表 <kbd>w</kbd></div><div>刷新数据 <kbd>r</kbd></div><div>关闭弹层/详情 <kbd>Esc</kbd></div></div>`)}
async function refresh(){const b=$("#btn-refresh");b.classList.add("busy");try{await api("/api/refresh",{method:"POST"});toast("刷新任务已启动");const t=setInterval(async()=>{const s=await api("/api/refresh_status");if(!s.running){clearInterval(t);b.classList.remove("busy");await Promise.all([loadStats(),loadFeed()]);toast(s.error?`刷新失败：${s.error}`:"刷新完成");}},1200)}catch(e){b.classList.remove("busy");toast(e.message)}}

document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{state.view=b.dataset.view;document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x===b));loadFeed()});
$("#mc").oninput=e=>{$("#mc-val").textContent=e.target.value;state.minCred=+e.target.value;loadFeed()};
let qt;$("#q").oninput=()=>{clearTimeout(qt);qt=setTimeout(loadFeed,250)};
$("#btn-sources").onclick=showSources;$("#btn-ai-radar").onclick=showAIRadar;$("#btn-research").onclick=showResearch;$("#btn-quality").onclick=showQuality;$("#btn-digest").onclick=showDigest;$("#btn-alerts").onclick=showAlerts;$("#btn-watchlist").onclick=showWatchlist;$("#btn-help").onclick=help;$("#btn-refresh").onclick=refresh;$("#btn-command").onclick=openCommands;
document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$("#command-palette").onclick=e=>{if(e.target===$("#command-palette"))closeCommands()};
$("#command-q").oninput=()=>{commandIndex=0;renderCommands()};
$("#command-q").onkeydown=e=>{const shown=[...document.querySelectorAll("[data-command]")];if(e.key==="ArrowDown"){e.preventDefault();commandIndex=Math.min(shown.length-1,commandIndex+1);renderCommands()}if(e.key==="ArrowUp"){e.preventDefault();commandIndex=Math.max(0,commandIndex-1);renderCommands()}if(e.key==="Enter"){e.preventDefault();shown[commandIndex]?.click()}if(e.key==="Escape"){e.preventDefault();closeCommands()}};
let gChord=0;
document.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();$("#command-palette").hidden?openCommands():closeCommands();return}if(!$("#command-palette").hidden)return;if(e.key==="Escape"){document.querySelector(".modal")?.remove();$("#detail").className="detail empty";$("#detail").innerHTML="选中一条事件<br>查看来源构成、证据与未知项";return}if(["INPUT","TEXTAREA"].includes(document.activeElement.tagName)){if(e.key==="Escape")document.activeElement.blur();return}const key=e.key.toLowerCase();if(key==="g"){gChord=Date.now();return}if(Date.now()-gChord<900&&(key==="a"||key==="p")){setMode(key==="a"?"public":"personal");gChord=0;return}gChord=0;if("123".includes(e.key))document.querySelectorAll(".tab")[+e.key-1]?.click();if(e.key==="/"){e.preventDefault();$("#q").focus()}if(e.key==="j")select(Math.min(state.items.length-1,state.selected+1));if(e.key==="k")select(Math.max(0,state.selected-1));if(e.key==="o"&&state.selected>=0)window.open(state.items[state.selected].url,"_blank","noopener");if(e.key==="a")showAlerts();if(e.key==="w")showWatchlist();if(e.key==="q")showQuality();if(e.key==="i")showAIRadar();if(e.key==="e")showResearch();if(e.key==="r")refresh();if(e.key==="s")showSources();if(e.key==="d")showDigest()};
setInterval(()=>$("#clock").textContent=new Date().toLocaleTimeString("zh-CN",{hour12:false}),1000);
initFilters(); setMode("personal"); loadStats().catch(e=>toast(`加载失败：${e.message}`)); loadMarkets(); loadAlertBadge(); setInterval(loadMarkets,120000); setInterval(loadAlertBadge,60000);
