"use strict";
const $ = (q, root = document) => root.querySelector(q);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const labels = {
  angle: "选题方向",
  title: "图文标题",
  post: "图文正文",
  cover: "封面文案",
  script: "口播脚本",
  cta: "互动引导",
};
const symbols = {
  angle: "01",
  title: "Aa",
  post: "¶",
  cover: "▧",
  script: "▷",
  cta: "↗",
};
const statusLabel = (b) =>
  b.stale
    ? "资料已变更"
    : { draft: "待审核", approved: "已通过", changes: "待修改" }[b.status] ||
      "待审核";
const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{ grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>', share: '<path d="M10 13l4-4M9 16l-2 2a4 4 0 01-6-6l5-5a4 4 0 016 0M15 8l2-2a4 4 0 016 6l-5 5a4 4 0 01-6 0" transform="translate(0 -1) scale(.95)"/>', download: '<path d="M12 3v12m-5-5l5 5 5-5M4 15v5h16v-5"/>', plus: '<path d="M12 5v14M5 12h14"/>', spark: '<path d="M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/>', check: '<path d="M5 12l4 4L19 6"/>' }[name] || ""}</svg>`;
const hash = new URLSearchParams(location.hash.slice(1));
let reviewToken =
  hash.get("review") || sessionStorage.getItem("raywork-review") || "";
if (hash.has("review")) {
  sessionStorage.setItem("raywork-review", reviewToken);
  sessionStorage.removeItem("raywork-admin");
}
if (hash.has("token")) {
  sessionStorage.setItem("raywork-admin", hash.get("token"));
  sessionStorage.removeItem("raywork-review");
  reviewToken = "";
}
if (location.hash) history.replaceState(null, "", location.pathname);
let token = sessionStorage.getItem("raywork-admin") || "",
  projects = [],
  current = null,
  selected = "post",
  tab = "work",
  health = { mode: "demo" },
  jobs = [],
  timer = null,
  toastTimer = null,
  busy = false;

function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("visible"), 4800);
}
async function api(path, options = {}) {
  const headers = {
    Authorization: `Bearer ${reviewToken || token}`,
    ...options.headers,
  };
  if (options.body && typeof options.body !== "string") {
    options.body = JSON.stringify(options.body);
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch("/api" + path, { ...options, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求未完成 (${response.status})，请检查输入或连接`,
    );
  }
  return options.blob ? response.blob() : response.json();
}
function bind(selector, event, fn) {
  document
    .querySelectorAll(selector)
    .forEach((el) => el.addEventListener(event, fn));
}
function dirty() {
  return (
    !reviewToken &&
    current &&
    (($("#block-text") &&
      $("#block-text").value !== current.blocks[selected].text) ||
      ($("#source-text") &&
        $("#source-text").value !== current.sources[0].text))
  );
}
function whenClick(selector, fn) {
  bind(selector, "click", async (e) => {
    if (busy) return;
    const el = e.currentTarget;
    if (dirty() && !["save-block", "save-source", "discard"].includes(el.id)) {
      toast("有未保存的编辑，请先保存或放弃修改");
      return;
    }
    busy = true;
    el.disabled = true;
    try {
      await fn(e, el);
    } catch (error) {
      toast(error.message);
    } finally {
      busy = false;
      if (el.isConnected) el.disabled = false;
    }
  });
}
function formValues(form) {
  return Object.fromEntries(new FormData(form));
}
function brand() {
  return '<div class="brand"><div class="brandmark">R</div><div class="brandname">RayWork<small>STUDIO</small></div></div>';
}
function formatTime(date) {
  return new Date(date).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function modal(html) {
  $("#modal-body").innerHTML = html;
  $("#modal").showModal();
  bind("[data-close]", "click", () => $("#modal").close());
}
function modalHead(title) {
  return `<div class="modalheading"><h2>${title}</h2><button class="quiet" data-close aria-label="关闭">×</button></div>`;
}
function footer() {
  return '<div class="footnote"><span>RayWork Studio · 让每一次交付，都有据可循。</span><span>v0.2 · 人工审核后交付</span></div>';
}

async function boot() {
  try {
    health = await api("/health");
  } catch {
    $("#app").innerHTML =
      '<div class="loading">暂时无法连接工作室，请确认服务已启动后刷新。</div>';
    return;
  }
  if (reviewToken) {
    try {
      current = await api("/review");
      renderReview();
    } catch (error) {
      login(error.message);
    }
    return;
  }
  if (!token) {
    login();
    return;
  }
  try {
    await loadProjects();
  } catch (error) {
    login(error.message);
  }
}
function login(error = "") {
  clearTimeout(timer);
  $("#app").innerHTML =
    `<div class="loginwrap"><div class="loginbox">${brand()}<div class="eyebrow">YOUR CONTENT WORKSPACE</div><h1>好内容，值得好好交付。</h1><p>连接你的工作室，开始资料整理、内容创作与客户审核。</p>${error ? `<div class="notice">${esc(error)}</div>` : ""}<form id="login"><label for="admin-token">工作室管理令牌</label><input id="admin-token" name="token" type="password" required minlength="24" placeholder="粘贴本地启动时生成的令牌" autocomplete="off"><button class="primary" type="submit">连接工作室 →</button></form><p>令牌保存在启动目录的 <code>.state/admin-token</code>。它只用于访问本地工作台，请勿当作客户审稿链接分享。</p><p>${health.mode === "demo" ? "当前为本地演示模式 · 无需模型 API Key" : "当前为真实模型模式 · API Key 仅在服务端使用"}</p></div></div>`;
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    token = formValues(e.target).token.trim();
    reviewToken = "";
    sessionStorage.removeItem("raywork-review");
    sessionStorage.setItem("raywork-admin", token);
    try {
      await loadProjects();
    } catch (error) {
      toast(error.message);
    }
  });
}
async function loadProjects(pid) {
  projects = await api("/projects");
  current =
    projects.find(
      (p) =>
        p.id ===
        (pid || current?.id || sessionStorage.getItem("raywork-project")),
    ) ||
    projects[0] ||
    null;
  if (current) {
    sessionStorage.setItem("raywork-project", current.id);
    jobs = await api(`/projects/${current.id}/jobs`);
  } else jobs = [];
  render();
  schedulePoll();
}
function shell(content) {
  return `<div class="shell"><aside class="sidebar">${brand()}<div class="navline active">${icon("grid")}内容工作台</div><div class="sidehead">交付项目 <button class="quiet" data-new aria-label="新建项目">+</button></div><div class="projectlist">${projects.map((p) => `<button class="projectlink ${p.id === current?.id ? "selected" : ""}" data-project="${p.id}" title="${esc(p.name)}">${esc(p.brief.brand)}</button>`).join("")}</div><div class="sidebottom"><div class="modebadge"><span class="dot"></span>${health.mode === "demo" ? "本地演示模式" : "真实模型模式"}<p>${health.mode === "demo" ? "模板演示 · 不产生模型调用费用" : health.configured ? "模型已配置 · 生成将调用 API" : "模型未配置 · 请检查服务端环境变量"}</p></div><div class="sidefooter"><span><span class="avatar">RX</span>我的工作室</span><button class="quiet" id="logout" aria-label="退出工作室">↪</button></div></div></aside><main><header class="topbar"><span>工作室<span class="sep">/</span><strong>${esc(current?.brief.brand || "欢迎回来")}</strong></span><div class="topright"><span class="dot"></span>本地保存<button class="small" data-new>${icon("plus")}新建项目</button></div><button class="small mobile-new" data-new>＋ 新建</button></header><div class="workspace">${content}${footer()}</div></main></div>`;
}
function render() {
  if (!current) {
    $("#app").innerHTML = shell(
      `<div class="welcomepanel"><div class="eyebrow">FROM BRIEF TO DELIVERY</div><h1>把创作，变成稳定的交付。</h1><p class="muted">一个项目，一份资料，六个可审核的内容模块。</p><div class="empty welcomeempty"><div class="welcomeart">R.</div><h2>你的第一个内容项目</h2><p>先体验一套虚构咖啡品牌的内容包，或从客户资料开始创建。所有内容先审核，再导出。</p><div class="actions"><button class="primary" id="demo">载入演示项目 →</button><button data-new>创建客户项目</button></div></div></div>`,
    );
    commonBindings();
    whenClick("#demo", async () => {
      const p = await api("/demo", { method: "POST" });
      await loadProjects(p.id);
      toast("演示项目已就绪；内容来自本地模板");
    });
    return;
  }
  const blocks = Object.values(current.blocks),
    approved = blocks.filter((b) => b.status === "approved" && !b.stale).length,
    unresolved = blocks.reduce(
      (n, b) => n + b.comments.filter((c) => !c.resolved).length,
      0,
    );
  const canExport = approved === 6 && !unresolved,
    exported =
      canExport &&
      current.events.some(
        (e) =>
          e.action === "exported" &&
          Object.keys(labels).every(
            (k) => e.versions?.[k] === current.blocks[k]?.version,
          ),
      );
  const heading = `<div class="heading"><div><div class="eyebrow">CONTENT DELIVERY / ${esc(current.brief.brand)}</div><h1>${esc(current.name)}</h1><p>${esc(current.brief.audience || "从品牌资料出发，把每个细节说清楚。")}</p></div><div class="actions"><button id="share">${icon("share")}客户审稿</button><button class="primary" id="export" ${canExport ? "" : "disabled"} title="全部 6 个模块通过审核后可导出">${icon("download")}导出交付包</button></div></div>`;
  const stages = [
    ["客户资料", `${current.sources.length} 份已就绪`, true],
    ["内容草稿", `${blocks.length} / 6 个模块`, !!blocks.length],
    ["审核确认", `${approved} / 6 已通过`, approved === 6],
    [
      "交付归档",
      exported
        ? "已导出，可再次交付"
        : canExport
          ? "可导出交付包"
          : "等待审核完成",
      exported,
    ],
  ];
  const pipeline = `<div class="pipeline">${stages.map((s, i) => `<div class="step ${s[2] ? "done" : i === stages.findIndex((x) => !x[2]) ? "current" : ""}"><div class="stepnum">${s[2] ? "✓" : i + 1}</div><div class="steptext">${s[0]}<small>${s[1]}</small></div></div>`).join("")}</div>`;
  const toolbar = `<div class="toolbar"><nav class="tabs" aria-label="项目视图">${[
    ["work", "内容工作台"],
    ["sources", "品牌与资料"],
    ["memory", "客户偏好"],
    ["activity", "活动记录"],
  ]
    .map(
      ([id, label]) =>
        `<button class="${tab === id ? "selected" : ""}" data-tab="${id}">${label}</button>`,
    )
    .join(
      "",
    )}</nav><span class="muted">${unresolved ? `${unresolved} 条待处理批注` : "每个模块独立修改 · 独立审核"}</span></div>`;
  const job = jobs[0],
    pending = job && ["queued", "running"].includes(job.status);
  const jobbar =
    job && (pending || job.status === "failed")
      ? `<div class="jobbar ${job.status === "failed" ? "failed" : ""}">${pending ? '<div class="spinner"></div>' : ""}<span>${pending ? "正在" + (job.target ? "重写「" + labels[job.target] + "」" : "生成内容包") + "，完成后将自动更新…" : esc(job.error)}</span>${job.status === "failed" ? `<button class="small" data-retry="${job.id}">重试任务</button>` : ""}</div>`
      : "";
  const body =
    tab === "work"
      ? workView()
      : tab === "sources"
        ? sourceView()
        : tab === "memory" ? memoryView() : activityView();
  $("#app").innerHTML = shell(heading + pipeline + toolbar + jobbar + body);
  commonBindings();
  memoryBindings();
  whenClick("[data-tab]", async (e, el) => {
    tab = el.dataset.tab;
    render();
  });
  whenClick("[data-key]", async (e, el) => {
    selected = el.dataset.key;
    render();
  });
  whenClick("#generate", async () => startJob(null));
  whenClick("#rewrite", async () =>
    startJob(selected, $("#rewrite-instruction").value),
  );
  whenClick("#share", shareModal);
  whenClick("#export", exportPack);
  whenClick("[data-retry]", async (e, el) => {
    await api(`/jobs/${el.dataset.retry}/retry`, { method: "POST" });
    await loadProjects();
  });
  whenClick("#save-block", async () => {
    await mutateBlock(
      "",
      {
        text: $("#block-text").value,
        version: current.blocks[selected].version,
      },
      "PUT",
    );
    toast("内容已保存，需重新审核");
  });
  whenClick("#discard", async () => render());
  whenClick("#approve", async () => {
    await mutateBlock("/approve", {
      version: current.blocks[selected].version,
    });
    toast("当前版本已通过审核");
  });
  whenClick("#reopen", async () => {
    await mutateBlock("/reopen", { version: current.blocks[selected].version });
    toast("已撤回审核，可继续编辑");
  });
  whenClick("#add-comment", async () => {
    await mutateBlock("/comments", {
      text: $("#comment-text").value,
      version: current.blocks[selected].version,
    });
    toast("批注已添加");
  });
  whenClick("[data-resolve]", async (e, el) => {
    await mutateBlock(`/comments/${el.dataset.resolve}/resolve`, {
      version: current.blocks[selected].version,
    });
    toast("已标记解决，请核对当前版本后审核");
  });
  whenClick("#save-source", async () => {
    const s = current.sources[0];
    await api(`/projects/${current.id}/sources/${s.id}`, {
      method: "PUT",
      body: { version: s.version, text: $("#source-text").value },
    });
    await loadProjects();
    toast("资料已保存，相关内容需重新核对");
  });
}
function commonBindings() {
  const sidebar = $(".sidebar");
  if (sidebar) {
    sidebar.insertAdjacentHTML(
      "beforeend",
      `<div class="mobile-context"><span>${health.mode === "demo" ? "本地模板演示" : "真实模型模式"}</span><select class="mobile-project" aria-label="切换项目"><option value="">选择项目</option>${projects.map((p) => `<option value="${p.id}" ${p.id === current?.id ? "selected" : ""}>${esc(p.brief.brand)}</option>`).join("")}</select></div>`,
    );
    bind(".mobile-project", "change", async (e) => {
      if (dirty()) {
        e.target.value = current.id;
        toast("请先保存或放弃当前修改");
        return;
      }
      if (e.target.value) {
        try {
          await loadProjects(e.target.value);
        } catch (error) {
          toast(error.message);
        }
      }
    });
  }
  whenClick("[data-new]", newProjectModal);
  whenClick("[data-project]", async (e, el) => {
    selected = "post";
    await loadProjects(el.dataset.project);
  });
  whenClick("#logout", async () => {
    sessionStorage.removeItem("raywork-admin");
    token = "";
    current = null;
    login();
  });
}
function card(b) {
  return `<button class="contentcard ${selected === b.key ? "selected" : ""}" data-key="${b.key}" aria-label="打开${esc(b.label)}"><div class="cardtop"><span class="cardlabel"><span class="cardicon">${symbols[b.key]}</span>${esc(b.label)}</span><span class="badge ${b.status}">${statusLabel(b)}</span></div><div class="${b.key === "cover" ? "covermock" : "cardtext"}">${esc(b.text)}</div><div class="cardfoot"><span>${b.citations.length} 处资料引用${b.comments.some((c) => !c.resolved) ? " · 有待处理批注" : ""}</span><span>v${b.version} ↗</span></div></button>`;
}
function workView() {
  if (!Object.keys(current.blocks).length)
    return `<div class="empty"><h2>资料已就绪，开始第一份内容包。</h2><p>一次生成选题、标题、正文、封面文案、口播脚本和互动引导。${health.mode === "demo" ? "当前使用本地模板；真实模型需在服务端配置后开启。" : "将向已配置的模型服务发送本项目资料。"}</p><button class="accent" id="generate">${icon("spark")}生成 6 个内容模块</button></div>`;
  if (!current.blocks[selected]) selected = Object.keys(current.blocks)[0];
  return `<div class="workgrid"><section class="cards" aria-label="内容模块">${Object.values(current.blocks).map(card).join("")}</section>${inspector(false)}</div>`;
}
function inspector(isReview) {
  const b = current.blocks[selected];
  if (!b) return "";
  const references = b.citations
    .map((c) => {
      const s = current.sources.find((s) => s.id === c.source_id);
      return `<div class="evidence"><strong>${esc(s?.title || "资料")} · v${c.source_version}</strong>“${esc(c.quote)}”</div>`;
    })
    .join("");
  const comments = b.comments.length
    ? b.comments
        .map(
          (c) =>
            `<div class="comment ${c.resolved ? "resolved" : ""}"><div class="commentmeta"><span>${esc(c.actor)} · v${c.version}</span><span>${c.resolved ? "已解决" : formatTime(c.at)}</span></div><div>${esc(c.text)}</div>${!isReview ? `<button class="small quiet" data-remember="${c.id}">整理为偏好规则</button>` : ""}${!isReview && !c.resolved ? `<button class="small quiet" data-resolve="${c.id}">标记已解决 ✓</button>` : ""}</div>`,
        )
        .join("")
    : '<p class="smallhelp">还没有批注。把修改意见留在对应模块，减少来回沟通。</p>';
  const isApproved = b.status === "approved",
    canApprove =
      !isApproved && !b.stale && !b.comments.some((c) => !c.resolved);
  return `<aside class="inspector"><div class="inspecthead"><div class="eyebrow">${isReview ? "CLIENT REVIEW" : "EDITOR / SELECTED BLOCK"}</div><h3>${esc(b.label)} <span class="badge ${b.status}">v${b.version} · ${statusLabel(b)}</span></h3></div><div class="inspectbody"><div>${b.stale ? '<div class="notice">来源资料已更新。请基于新资料重写这个模块，再提交审核。</div>' : ""}${isReview ? `<div class="reviewtext">${esc(b.text)}</div>` : `<label for="block-text" class="eyebrow">内容编辑</label><textarea id="block-text" rows="10" maxlength="12000" ${isApproved ? "readonly" : ""}>${esc(b.text)}</textarea><div class="actions">${isApproved ? '<button id="reopen">撤回审核并编辑</button>' : '<button id="save-block">保存修改</button><button class="quiet" id="discard">放弃修改</button>'}</div>`}<div class="actions"><button class="primary" id="approve" ${canApprove ? "" : "disabled"}>${icon("check")}${isApproved ? "当前版本已通过" : "通过当前版本"}</button></div>${b.approval ? `<p class="smallhelp">${esc(b.approval.actor)} · ${formatTime(b.approval.at)}</p>` : ""}${!isReview && !isApproved ? `<label for="rewrite-instruction">只重写这个模块</label><textarea id="rewrite-instruction" rows="2" maxlength="2000" placeholder="例如：标题更具体，保留十分钟手冲这个场景"></textarea><div class="actions"><button class="accent" id="rewrite">${icon("spark")}局部重写</button></div><p class="smallhelp">${health.mode === "demo" ? "演示模式会附上修改要求，不会进行智能改写。" : "只更新当前模块，其他模块保留。"}</p>` : ""}${!isReview ? `<div class="sectionlabel">本版生成时的偏好规则</div><p class="smallhelp">${(b.memory_context || []).map(r => esc(r.text)).join("；") || "未使用已确认规则"}${b.usage?.mode === "demo" ? " · 模板演示不执行规则" : ""}</p>` : ""}<div class="sectionlabel">资料依据</div>${references}<p class="smallhelp">摘录已匹配原文；是否支持整段表述，仍需人工核对。</p></div><div><div class="sectionlabel">修改批注</div>${comments}<label for="comment-text">新增批注</label><textarea id="comment-text" rows="2" maxlength="2000" placeholder="明确指出希望调整的内容…"></textarea><div class="actions"><button id="add-comment">提交批注</button></div>${
    !isReview && b.history.length
      ? `<div class="sectionlabel">历史版本 · ${b.history.length}</div>${[
          ...b.history,
        ]
          .reverse()
          .map(
            (h) =>
              `<details class="history"><summary>v${h.version} · ${formatTime(h.updated_at)}</summary><pre>${esc(h.text)}</pre></details>`,
          )
          .join("")}`
      : ""
  }${!isReview && b.usage ? `<div class="sectionlabel">最近生成</div><p class="smallhelp">${esc(b.usage.model)} · ${b.usage.elapsed_seconds ?? "—"}s<br>${b.usage.input_tokens} 输入 / ${b.usage.output_tokens} 输出 tokens<br>${b.usage.estimated_cost_usd === null ? "成本未配置，未估算" : `估算 $${Number(b.usage.estimated_cost_usd).toFixed(4)}`}</p>` : ""}</div></div></aside>`;
}

function memoryView() {
  const rules = current.memory || [];
  const candidates = projects.filter(p => p.id !== current.id && p.brief.brand.trim().toLowerCase() === current.brief.brand.trim().toLowerCase() && (p.memory || []).some(r => r.status === "active"));
  return `<section class="panel"><div class="eyebrow">CUSTOMER MEMORY</div><h2>这位客户的要求，下次还记得。</h2><p class="muted">把批注整理成长期适用的规则，确认后才用于后续生成。仅保存本项目；同品牌项目可手动复用。</p><div class="actions"><button class="primary" id="new-rule">＋ 新增偏好规则</button></div><div class="notice">规则不提供产品事实，也不保证模型完全遵守。确认或停用只影响下一次生成，不会修改已有内容或审核结果。${health.mode === "demo" ? "当前为模板演示，规则仅作记录，不执行智能改写。" : "生成时将向模型发送适用的已确认规则，请继续人工审核。"}</div>${candidates.length ? `<label for="memory-source">复用同品牌项目的已确认规则</label><select id="memory-source">${candidates.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select><div class="actions"><button id="import-rules">导入为待确认规则</button></div>` : ""}<div class="memory-list">${rules.length ? [...rules].reverse().map(r => `<article class="memory-card"><div class="actions"><span class="badge">${{pending:"待确认",active:"已确认",retired:"已停用"}[r.status]}</span><span class="muted">${r.scope === "all" ? "全部模块" : labels[r.scope]} · v${r.version}</span></div><p>${esc(r.text)}</p><p class="smallhelp">${r.origin.type === "comment" ? "来源批注：" + esc(r.origin.text) : r.origin.type === "import" ? "复用自：" + esc(r.origin.project_name) : "工作室手动整理"}</p><div class="actions">${r.status === "pending" ? `<button class="primary small" data-rule="${r.id}" data-decision="active">确认用于后续生成</button>` : ""}${r.status !== "retired" ? `<button class="small" data-rule="${r.id}" data-decision="retired">${r.status === "pending" ? "放弃此规则" : "停用规则"}</button>` : ""}</div></article>`).join("") : `<p class="muted">还没有偏好规则。可从内容模块的批注中整理，也可以手动新增。</p>`}</div></section>`;
}
function ruleModal(comment) {
  modal(`${modalHead("整理客户偏好规则")}<p class="muted">只记录长期适用的表达要求，例如“标题不使用夸张承诺”。一次性的价格或活动日期应保留在资料中。</p><form id="rule-form"><label for="rule-text">规则内容</label><textarea id="rule-text" name="text" rows="4" maxlength="1000" required>${esc(comment?.text || "")}</textarea><label for="rule-scope">适用模块</label><select id="rule-scope" name="scope"><option value="all">全部模块</option>${Object.entries(labels).map(([k,v]) => `<option value="${k}" ${comment && k === selected ? "selected" : ""}>${v}</option>`).join("")}</select><p class="smallhelp">保存后进入待确认，不会自动解决原批注。</p><div class="modalfoot"><button type="button" data-close>取消</button><button class="primary" type="submit">保存为待确认</button></div></form>`);
  $("#rule-form").addEventListener("submit", async e => {
    e.preventDefault();
    const button = $("button[type=submit]", e.target);
    button.disabled = true;
    try {
      await api(`/projects/${current.id}/memory`, {method:"POST", body:{...formValues(e.target), comment_id:comment?.id || null}});
      $("#modal").close(); tab = "memory"; await loadProjects(); toast("规则已保存，请确认后使用");
    } catch (error) {toast(error.message);} finally {button.disabled = false;}
  });
}
function memoryBindings() {
  whenClick("#new-rule", async () => ruleModal());
  whenClick("[data-remember]", async (e, el) => ruleModal(current.blocks[selected].comments.find(c => c.id === el.dataset.remember)));
  whenClick("[data-rule]", async (e, el) => {
    const rule = current.memory.find(r => r.id === el.dataset.rule);
    await api(`/projects/${current.id}/memory/${rule.id}`, {method:"POST", body:{version:rule.version,status:el.dataset.decision}});
    await loadProjects(); toast("规则状态已更新，仅影响后续生成");
  });
  whenClick("#import-rules", async () => {
    await api(`/projects/${current.id}/memory/import`, {method:"POST", body:{project_id:$("#memory-source").value}});
    await loadProjects(); toast("已复用，请逐条确认适用范围");
  });
}

function sourceView() {
  const s = current.sources[0];
  return `<div class="sourcegrid"><section class="panel"><h3>${esc(s.title)} <span class="badge">v${s.version}</span></h3><p class="muted">把品牌确认过的事实写在这里。修改会使引用它的内容重新待审。</p><label for="source-text">原始资料</label><textarea id="source-text" rows="16" maxlength="20000">${esc(s.text)}</textarea><div class="actions"><button class="primary" id="save-source">保存资料</button><button class="quiet" id="discard">放弃修改</button></div></section><aside class="panel"><div class="eyebrow">BRAND CONTEXT</div><h2>${esc(current.brief.brand)}</h2>${[
    ["audience", "目标受众"],
    ["tone", "品牌语气"],
    ["avoid", "禁用表达与边界"],
  ]
    .map(
      ([k, l]) =>
        `<div class="briefrow"><small>${l}</small>${esc(current.brief[k] || "未设置")}</div>`,
    )
    .join(
      "",
    )}<p class="smallhelp">每个项目对应一份品牌简报和一个主资料文档。需要不同定位时，请新建项目。</p></aside></div>`;
}
function activityView() {
  return `<div class="panel timeline"><h3>交付过程，可追溯</h3>${[
    ...current.events,
  ]
    .reverse()
    .map(
      (e) =>
        `<div class="timelineitem"><time>${formatTime(e.at)}</time><div>${esc(e.detail)}<small>${esc(e.actor)}</small></div></div>`,
    )
    .join(
      "",
    )}<div class="jobslist"><h3>生成任务记录</h3>${jobs.length ? jobs.map((j) => `<div class="jobitem">${j.target ? labels[j.target] : "完整内容包"} · ${esc({ succeeded: "已完成", failed: "失败", running: "运行中", queued: "排队中" }[j.status])} · 第 ${j.attempts} 次尝试${j.error ? `<p class="smallhelp">${esc(j.error)}</p>` : ""}${j.usage ? `<p class="smallhelp">${esc(j.usage.model)} · ${j.usage.elapsed_seconds}s · ${j.usage.input_tokens + j.usage.output_tokens} tokens</p>` : ""}${j.status === "failed" ? `<div class="actions"><button class="small" data-retry="${j.id}">重试任务</button></div>` : ""}</div>`).join("") : '<p class="smallhelp">暂无生成任务。载入演示项目使用本地预置模板。</p>'}</div></div>`;
}
async function mutateBlock(suffix, body, method = "POST") {
  current = await api(`/projects/${current.id}/blocks/${selected}${suffix}`, {
    method,
    body,
  });
  await loadProjects();
}
async function startJob(target, instruction = "") {
  if (
    target &&
    $("#block-text") &&
    $("#block-text").value !== current.blocks[target].text
  ) {
    toast("请先保存编辑内容，再进行局部重写");
    return;
  }
  await api(`/projects/${current.id}/jobs`, {
    method: "POST",
    body: {
      key: crypto.randomUUID(),
      target,
      instruction,
      version: target ? current.blocks[target].version : null,
    },
  });
  await loadProjects();
}
function schedulePoll() {
  clearTimeout(timer);
  if (!jobs.some((j) => ["queued", "running"].includes(j.status))) return;
  const pid = current.id;
  timer = setTimeout(async () => {
    try {
      if (current?.id !== pid) return;
      const updated = await api(`/projects/${pid}/jobs`);
      const completed = updated.some(
        (j) =>
          ["succeeded", "failed"].includes(j.status) &&
          jobs.some(
            (old) =>
              old.id === j.id && ["queued", "running"].includes(old.status),
          ),
      );
      jobs = updated;
      if (completed) {
        if (dirty()) {
          toast("生成任务已结束；请先保存正在编辑的内容，再查看结果");
          return;
        }
        await loadProjects(pid);
        toast(
          jobs[0].status === "succeeded"
            ? "内容已更新，请审核新版本"
            : "任务失败，可查看原因后重试",
        );
      } else schedulePoll();
    } catch (error) {
      toast(error.message);
      schedulePoll();
    }
  }, 1500);
}
function newProjectModal() {
  modal(
    `${modalHead("创建内容交付项目")}<p class="muted">从一份明确的客户资料开始。首版先把一个交付周期做好。</p><form id="new-project"><label for="project-name">项目名称</label><input id="project-name" name="name" required maxlength="100" placeholder="例如：秋日上新 · 第 01 周内容包"><div class="formrow"><div><label for="brand-name">品牌名称</label><input id="brand-name" name="brand" required maxlength="100" placeholder="客户品牌"></div><div><label for="audience">目标受众</label><input id="audience" name="audience" maxlength="500" placeholder="内容是写给谁的？"></div></div><label for="tone">品牌语气</label><input id="tone" name="tone" maxlength="500" value="具体、真诚、有分寸"><label for="avoid">禁用表达与边界</label><input id="avoid" name="avoid" maxlength="1000" value="不编造产品效果、价格和用户评价"><label for="source-input">客户资料 / 已确认的产品事实</label><textarea id="source-input" name="source_text" rows="6" required maxlength="20000" placeholder="产品信息、内容主题、使用场景、已确认的限制条件…"></textarea><div class="modalfoot"><button type="button" data-close>取消</button><button class="primary" type="submit">创建项目</button></div></form>`,
  );
  $("#new-project").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", e.target);
    button.disabled = true;
    try {
      const p = await api("/projects", {
        method: "POST",
        body: formValues(e.target),
      });
      $("#modal").close();
      tab = "work";
      await loadProjects(p.id);
      toast("项目已创建，资料已保存");
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });
}
async function shareModal() {
  const share = await api(`/projects/${current.id}/share`, { method: "POST" });
  const url = location.origin + "/#review=" + share.token;
  modal(
    `${modalHead("让客户直接审核内容")}<p class="muted">持有链接的人可以查看此项目的当前内容和资料、添加批注、通过审核。链接有效期为 72 小时。</p><label for="share-url">项目专属审稿链接</label><input id="share-url" readonly value="${esc(url)}"><p class="smallhelp">有效至 ${formatTime(share.expires)}。本地启动时，此链接只能在能访问当前服务的设备上使用；发给外部客户前需要部署并启用 HTTPS。</p><div class="modalfoot"><button class="danger" id="revoke">撤销本项目全部链接</button><button class="primary" id="copy-link">复制链接</button></div>`,
  );
  whenClick("#copy-link", async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast("审稿链接已复制");
    } catch {
      $("#share-url").select();
      toast("请复制选中的链接");
    }
  });
  whenClick("#revoke", async () => {
    await api(`/projects/${current.id}/share`, { method: "DELETE" });
    $("#modal").close();
    toast("所有审稿链接已撤销");
  });
}
async function exportPack() {
  const blob = await api(`/projects/${current.id}/export`, { blob: true });
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = "raywork-delivery.zip";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  await loadProjects();
  toast("交付包已导出，包含文本、资料依据和审核记录");
}
function renderReview() {
  if (!current.blocks[selected]) selected = Object.keys(current.blocks)[0];
  $("#app").innerHTML =
    `<div class="reviewbanner">客户审稿空间 · 每条批注和确认都对应具体内容版本</div><div class="reviewmain"><div class="heading">${brand()}<div><div class="eyebrow">${esc(current.brief.brand)} / CLIENT REVIEW</div><h1>${esc(current.name)}</h1></div></div>${Object.keys(current.blocks).length ? `<div class="workgrid"><section class="cards">${Object.values(current.blocks).map(card).join("")}</section>${inspector(true)}</div>` : '<div class="empty">工作室还未生成内容，请稍后刷新。</div>'}${footer()}</div>`;
  whenClick("[data-key]", async (e, el) => {
    selected = el.dataset.key;
    renderReview();
  });
  whenClick("#add-comment", async () => {
    current = await api(`/review/blocks/${selected}/comments`, {
      method: "POST",
      body: {
        version: current.blocks[selected].version,
        text: $("#comment-text").value,
      },
    });
    renderReview();
    toast("批注已发送给工作室");
  });
  whenClick("#approve", async () => {
    current = await api(`/review/blocks/${selected}/approve`, {
      method: "POST",
      body: { version: current.blocks[selected].version },
    });
    renderReview();
    toast("当前版本已通过审核");
  });
}
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "s" && $("#save-block")) {
    e.preventDefault();
    $("#save-block").click();
  }
});
window.addEventListener("beforeunload", (e) => {
  if (dirty()) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener("unhandledrejection", (e) => {
  toast(e.reason?.message || "操作未完成，请重试");
});
boot();
