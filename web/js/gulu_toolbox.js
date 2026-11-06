/**
 * ComfyUI 图像后处理工具箱 · 前端
 * ------------------------------------------------------------------
 * 只做一件事：参数显隐联动。
 * 切换「保存格式 / 水印来源 / 水印位置 / 保存位置」时，
 * 只保留当前用得到的参数，面板保持清爽。
 */

const ITP_APP =
  (typeof window !== "undefined" && window.comfyAPI && window.comfyAPI.app)
    ? (window.comfyAPI.app.app || window.comfyAPI.app)
    : null;

const ITP_MIN_W = 320;

/**
 * 按需拿 ComfyUI 的 app 实例。
 * 不能在脚本加载时缓存 —— 新版前端的 comfyAPI.app 层级会变，
 * 早取会拿到没有 api / queuePrompt 的命名空间对象。
 */
function itpApp() {
  let c = window.comfyAPI && window.comfyAPI.app;
  for (let i = 0; i < 6 && c; i++) {
    if (typeof c.queuePrompt === "function" && c.graph && c.api) return c;
    c = c.app || null;
  }
  return null;
}

/* 每个节点各自一份配置：
 *   rules —— 显隐联动规则 { 控制控件: { 被控控件: 显示条件 } }
 *   hide  —— 永远不显示的控件（内部参数）
 *   panel —— 节点下方的小面板："size" = 显示计算后的宽高
 */
const ITP_NODES = {
  ImagePostExportCN: {
    rules: {},          // 下面填
    hide: [],
  },
  ImageBatchLoopLoader: {
    rules: {},
    hide: [],
    loop: true,         // 前端驱动顺序推进
    panel: "loader",    // 图 + 下方信息条
  },
  ImageEdgeResizeCN: {
    rules: {},
    hide: [],
    panel: "size",      // 显示计算后的宽高
  },
  LoRACycleCN: {
    rules: {},
    hide: ["循环位置"],  // 内部推进参数，界面上不显示
    loop: true,          // 前端驱动轮转
    panel: "lora",       // 显示 第 x/y 个 + 当前 LoRA
  },
  PromptBatchLoopLoader: {
    rules: {},           // 下面填
    hide: ["循环位置"],
    loop: true,          // 和图片批量同一套推进机制
    panel: "prompt",     // 显示 第 x/y 条 + 文件名 + 提示词预览
  },
};


const ITP_SIZE_PANEL_H = 64;

/* ==================================================================
 * 显隐规则：{ 控制控件: { 被控控件: 显示条件 } }
 * 条件可以是 字符串 / 数组 / 函数
 * ================================================================== */
const ITP_EXPORT_RULES = {
  保存格式: {
    JPG质量: ["JPG"],
    PNG压缩等级: ["PNG"],
    PNG写入工作流: ["PNG"],
  },

  水印来源: {
    水印文件: ["水印文件"],
    水印位置: ["水印文件", "接入图像端口"],
    水印宽度: ["水印文件", "接入图像端口"],
    不透明度: ["水印文件", "接入图像端口"],
    旋转角度: ["水印文件", "接入图像端口"],
    水印边距X: ["水印文件", "接入图像端口"],
    水印边距Y: ["水印文件", "接入图像端口"],
    水印间距: ["水印文件", "接入图像端口"],
  },

  水印位置: {
    // 平铺时不用边距，九宫格时不用间距
    水印边距X: (v) => v !== "平铺",
    水印边距Y: (v) => v !== "平铺",
    水印间距: ["平铺"],
  },

  保存位置: {
    子文件夹: (v) => typeof v === "string" && v.indexOf("自定义") < 0,
    自定义路径: (v) => typeof v === "string" && v.indexOf("自定义") >= 0,
  },
};

ITP_NODES.ImagePostExportCN.rules = ITP_EXPORT_RULES;

/* 提示词批量导入：只有选「自定义」时才显示自定义分隔符那一栏 */
const ITP_PROMPT_RULES = {
  分隔符: {
    自定义分隔符: ["自定义"],
  },
};

ITP_NODES.PromptBatchLoopLoader.rules = ITP_PROMPT_RULES;

/* ==================================================================
 * 工具
 * ================================================================== */

function itpMatch(cond, value) {
  if (typeof cond === "function") {
    try { return !!cond(value); } catch (e) { return true; }
  }
  if (Array.isArray(cond)) return cond.indexOf(value) >= 0;
  return cond === value;
}

function itpShow(widget, show) {
  if (!widget) return;
  widget.hidden = !show;
  // 文本 / 数字类控件是 DOM 元素，要一起隐藏，否则会留一块空白
  const els = [widget.element, widget.inputEl, widget.labelEl];
  for (const el of els) {
    if (el && el.style) el.style.display = show ? "" : "none";
  }
}

function itpFit(node) {
  try {
    const cs = node.computeSize();
    const w = Math.max(ITP_MIN_W, node.size[0] || 0);
    node.setSize([w, Math.max(120, cs[1])]);
  } catch (e) { /* 忽略 */ }
}

function itpDirty(node) {
  try {
    if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
    else if (node.graph && node.graph.setDirtyCanvas) node.graph.setDirtyCanvas(true, true);
  } catch (e) { /* 忽略 */ }
}

/** 按当前参数值刷新一次显隐 */
function itpRefresh(node, rules) {
  const find = (name) => node.widgets && node.widgets.find((w) => w.name === name);
  for (const ctrlName of Object.keys(rules || {})) {
    const ctrl = find(ctrlName);
    if (!ctrl) continue;
    const map = rules[ctrlName];
    for (const targetName of Object.keys(map)) {
      itpShow(find(targetName), itpMatch(map[targetName], ctrl.value));
    }
  }
}

/* ==================================================================
 * 尺寸面板（按边压缩节点专用）
 * 只显示计算后的宽高文字，不出图。
 * 颜色全部用 inherit / opacity，跟着主题走，深浅色都不糊。
 * ================================================================== */

function itpDiv(css, text) {
  const el = document.createElement("div");
  el.style.cssText = css;
  if (text != null) el.textContent = text;
  return el;
}

function itpBuildSizePanel(node) {
  const wrap = itpDiv(
    "display:flex;flex-direction:column;justify-content:center;gap:2px;" +
    "padding:4px 8px 8px;box-sizing:border-box;color:inherit;" +
    "font-family:system-ui,'Segoe UI','Microsoft YaHei',sans-serif;"
  );
  const big = itpDiv(
    "font-size:19px;font-weight:600;line-height:1.25;text-align:center;" +
    "letter-spacing:.6px;color:inherit;white-space:nowrap;overflow:hidden;" +
    "text-overflow:ellipsis;",
    "— × —"
  );
  const sub = itpDiv(
    "font-size:11px;line-height:1.4;text-align:center;opacity:.6;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;",
    "执行后显示计算出的宽高"
  );
  wrap.appendChild(big);
  wrap.appendChild(sub);

  const widget = node.addDOMWidget("计算结果", "gulu_size", wrap, {
    serialize: false,
    hideOnZoom: false,
  });
  widget.computeSize = function (width) {
    return [Math.max(120, width || 240), ITP_SIZE_PANEL_H];
  };

  node.__guluSizeUI = { big, sub };
}

function itpUpdateSizePanel(node, message) {
  const ui = node.__guluSizeUI;
  if (!ui) return;
  const list = message && message.gulu_resize;
  const item = Array.isArray(list) ? list[0] : null;
  if (!item) return;

  ui.big.textContent = `${item.width} × ${item.height}`;

  const parts = [];
  if (item.scaled) {
    parts.push(`原图 ${item.src_width}×${item.src_height} 已缩放`);
  } else {
    parts.push(`未缩放（原图 ${item.src_width}×${item.src_height}）`);
  }
  parts.push(item.align > 1 ? `对齐 ${item.align}` : "不对齐");
  if (item.count > 1) parts.push(`${item.count} 张`);
  ui.sub.textContent = parts.join(" · ");

  itpDirty(node);
}

/* ==================================================================
 * 加载器面板（批量顺序导入专用）
 * 图在上，分辨率 / 文件名 / 第 x/y 张 在图的正下方。
 * ================================================================== */

const ITP_LOADER_PREVIEW_H = 200;
const ITP_LOADER_INFO_H = 46;

function itpBuildLoaderPanel(node) {
  const wrap = itpDiv(
    "display:flex;flex-direction:column;gap:0;padding:0 6px 6px;" +
    "box-sizing:border-box;color:inherit;" +
    "font-family:system-ui,'Segoe UI','Microsoft YaHei',sans-serif;"
  );
  const frame = itpDiv(
    `position:relative;width:100%;height:${ITP_LOADER_PREVIEW_H}px;border-radius:8px;` +
    "background:rgba(0,0,0,.22);border:1px solid rgba(128,128,128,.28);" +
    "overflow:hidden;display:flex;align-items:center;justify-content:center;cursor:zoom-in;"
  );
  const img = document.createElement("img");
  img.style.cssText = "max-width:100%;max-height:100%;display:block;";
  img.style.display = "none";
  img.draggable = false;
  const hint = itpDiv(
    "position:absolute;font-size:12px;color:inherit;opacity:.4;pointer-events:none;",
    "运行后显示当前图片"
  );
  frame.appendChild(img);
  frame.appendChild(hint);

  const info = itpDiv(
    `height:${ITP_LOADER_INFO_H}px;padding:6px 2px 0;box-sizing:border-box;` +
    "display:flex;flex-direction:column;gap:1px;overflow:hidden;"
  );
  const l1 = itpDiv(
    "font-size:15px;font-weight:600;line-height:1.25;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;",
    "— × —"
  );
  const l2 = itpDiv(
    "font-size:11px;line-height:1.35;opacity:.62;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;",
    "等待运行"
  );
  info.appendChild(l1);
  info.appendChild(l2);

  wrap.appendChild(frame);
  wrap.appendChild(info);

  const widget = node.addDOMWidget("预览与信息", "gulu_loader_panel", wrap, {
    serialize: false,
    hideOnZoom: false,
  });
  widget.computeSize = function (width) {
    return [Math.max(140, width || 240), ITP_LOADER_PREVIEW_H + ITP_LOADER_INFO_H + 8];
  };

  frame.addEventListener("click", () => {
    if (node.__guluUrl) window.open(node.__guluUrl, "_blank");
  });

  node.__guluLoaderUI = { img, hint, l1, l2 };
}

function itpUpdateLoaderPanel(node, message) {
  const ui = node.__guluLoaderUI;
  if (!ui) return;
  const list = message && message.gulu_show;
  const item = Array.isArray(list) ? list[0] : null;
  if (!item) return;

  if (item.filename) {
    const base = "/view?filename=" + encodeURIComponent(item.filename) +
                 "&type=temp&subfolder=";
    ui.img.src = base + "&_=" + Date.now();     // 加时间戳，避免浏览器拿缓存旧图
    ui.img.style.display = "block";
    ui.hint.style.display = "none";
    node.__guluUrl = base;
  }

  ui.l1.textContent = `${item.width} × ${item.height}`;
  const parts = [item.name];
  if (item.mode === "batch") {
    parts.push(`共 ${item.total} 张`);
  } else {
    parts.push(`第 ${item.index + 1}/${item.total} 张`);
  }
  ui.l2.textContent = parts.join("  ·  ");
  itpDirty(node);
}

/* ==================================================================
 * LoRA 编辑器（LoRA 轮转节点专用）
 * 下拉添加 / 开关 / 左右调强度 / 删除，数据写回隐藏的「LoRA清单」widget
 * ================================================================== */

const ITP_LORA_ROW_H = 27;
const ITP_LORA_MAX_ROWS = 7;

let ITP_LORA_FILES = null;

function itpLoraFiles() {
  if (ITP_LORA_FILES) return Promise.resolve(ITP_LORA_FILES);
  return fetch("/object_info/LoraLoader")
    .then((r) => r.json())
    .then((j) => {
      const def = j.LoraLoader || {};
      const list = (def.input && def.input.required && def.input.required.lora_name)
        ? def.input.required.lora_name[0] : [];
      ITP_LORA_FILES = Array.isArray(list) ? list : [];
      return ITP_LORA_FILES;
    })
    .catch(() => { ITP_LORA_FILES = []; return []; });
}

function itpBtn(label, css) {
  const b = document.createElement("button");
  b.textContent = label;
  b.style.cssText =
    "flex:none;height:20px;min-width:20px;padding:0 4px;border-radius:5px;" +
    "border:1px solid rgba(128,128,128,.35);background:rgba(255,255,255,.06);" +
    "color:inherit;font-size:11px;line-height:1;cursor:pointer;" + (css || "");
  b.addEventListener("mouseenter", () => { b.style.background = "rgba(255,255,255,.14)"; });
  b.addEventListener("mouseleave", () => { b.style.background = "rgba(255,255,255,.06)"; });
  return b;
}

function itpLoraPanelHeight(count, bulkOpen) {
  const rows = Math.min(Math.max(count, 1), ITP_LORA_MAX_ROWS);
  let h = 36 + rows * ITP_LORA_ROW_H + (count ? 0 : 22) + 10;
  if (bulkOpen) h += 196;   // 搜索框 + 复选列表 + 按钮行
  return h;
}

function itpBuildLoraEditor(node) {
  const listW = node.widgets && node.widgets.find((w) => w.name === "LoRA清单");
  if (listW) itpShow(listW, false);      // 原始输入框只当数据容器，不显示

  let items = [];
  try {
    const parsed = JSON.parse((listW && listW.value) || "[]");
    if (Array.isArray(parsed)) {
      items = parsed.filter((x) => x && x.lora).map((x) => ({
        lora: String(x.lora),
        on: x.on !== false,
        strength: typeof x.strength === "number" ? x.strength : 1.0,
      }));
    }
  } catch (e) { items = []; }

  const wrap = itpDiv(
    "display:flex;flex-direction:column;gap:5px;padding:2px 6px 8px;" +
    "box-sizing:border-box;color:inherit;" +
    "font-family:system-ui,'Segoe UI','Microsoft YaHei',sans-serif;"
  );

  const head = itpDiv("display:flex;gap:4px;align-items:center;");
  const sel = document.createElement("select");
  sel.style.cssText =
    "flex:1;min-width:0;height:23px;border-radius:6px;padding:0 4px;" +
    "border:1px solid rgba(128,128,128,.35);background:rgba(0,0,0,.28);" +
    "color:inherit;font-size:11px;outline:none;cursor:pointer;";
  const addBtn = itpBtn("+ 添加", "height:23px;padding:0 8px;font-size:11px;");
  const bulkBtn = itpBtn("批量", "height:23px;padding:0 7px;font-size:11px;");
  head.appendChild(sel);
  head.appendChild(addBtn);
  head.appendChild(bulkBtn);

  /* ---------------- 批量添加区 ---------------- */
  let bulkOpen = false;
  const bulkSel = new Set();
  let bulkQuery = "";
  let allFiles = [];

  const bulkBox = itpDiv(
    "display:none;flex-direction:column;gap:4px;padding:5px;border-radius:6px;" +
    "background:rgba(0,0,0,.18);border:1px solid rgba(128,128,128,.22);"
  );
  const qInput = document.createElement("input");
  qInput.type = "text";
  qInput.placeholder = "搜索：输入路径关键词过滤（如 Krea2_自建人物）";
  qInput.style.cssText =
    "width:100%;box-sizing:border-box;height:23px;border-radius:5px;padding:0 6px;" +
    "border:1px solid rgba(128,128,128,.35);background:rgba(0,0,0,.28);" +
    "color:inherit;font-size:11px;outline:none;";
  const bList = itpDiv(
    "display:flex;flex-direction:column;gap:1px;overflow-y:auto;overflow-x:hidden;" +
    "max-height:120px;padding:2px;border-radius:5px;background:rgba(0,0,0,.16);"
  );
  const bFoot = itpDiv("display:flex;gap:4px;align-items:center;");
  const addSelBtn = itpBtn("添加选中", "height:22px;padding:0 6px;font-size:11px;");
  const allMatchBtn = itpBtn("全选匹配", "height:22px;padding:0 6px;font-size:11px;");
  const bHint = itpDiv("flex:1;min-width:0;font-size:10px;opacity:.5;text-align:right;", "");
  bFoot.appendChild(addSelBtn);
  bFoot.appendChild(allMatchBtn);
  bFoot.appendChild(bHint);
  bulkBox.appendChild(qInput);
  bulkBox.appendChild(bList);
  bulkBox.appendChild(bFoot);

  const rowsBox = itpDiv(
    "display:flex;flex-direction:column;gap:3px;overflow-y:auto;overflow-x:hidden;" +
    "max-height:" + (ITP_LORA_ROW_H * ITP_LORA_MAX_ROWS) + "px;"
  );

  wrap.appendChild(head);
  wrap.appendChild(bulkBox);
  wrap.appendChild(rowsBox);

  const widget = node.addDOMWidget("LoRA面板", "gulu_lora_editor", wrap, {
    serialize: false,
    hideOnZoom: false,
  });
  function panelH() { return itpLoraPanelHeight(items.length, bulkOpen); }
  widget.computeSize = function (width) {
    return [Math.max(300, width || 340), panelH()];
  };

  function save() {
    if (listW) listW.value = JSON.stringify(items);
    itpFit(node);
    itpDirty(node);
  }

  /** 批量区：按搜索词渲染复选列表 */
  function renderBulk() {
    const q = bulkQuery.trim().toLowerCase();
    const matched = q ? allFiles.filter((f) => f.toLowerCase().indexOf(q) >= 0) : allFiles;
    const shown = matched.slice(0, 150);

    bList.innerHTML = "";
    if (!shown.length) {
      bList.appendChild(itpDiv(
        "font-size:11px;opacity:.45;text-align:center;padding:8px 0;", "没有匹配的 LoRA"
      ));
    }
    for (const f of shown) {
      const row = itpDiv("display:flex;align-items:center;gap:4px;height:19px;padding:0 2px;");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = bulkSel.has(f);
      cb.style.cssText = "flex:none;margin:0;cursor:pointer;accent-color:#7c9cff;";
      cb.addEventListener("change", () => {
        if (cb.checked) bulkSel.add(f); else bulkSel.delete(f);
        updateBulkFoot(matched.length);
      });
      const nm = itpDiv(
        "flex:1;min-width:0;font-size:10px;white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis;opacity:.85;", f.split(/[\\/]/).pop()
      );
      nm.title = f;
      row.appendChild(cb);
      row.appendChild(nm);
      bList.appendChild(row);
    }
    updateBulkFoot(matched.length, shown.length);
  }

  function updateBulkFoot(matchedCount, shownCount) {
    const n = bulkSel.size;
    addSelBtn.textContent = n ? `添加选中 ${n} 个` : "添加选中";
    addSelBtn.style.opacity = n ? "1" : ".45";
    const parts = [];
    if (typeof matchedCount === "number") parts.push(`匹配 ${matchedCount}`);
    if (typeof shownCount === "number" && matchedCount > shownCount) {
      parts.push(`只显示前 ${shownCount}，缩小搜索范围`);
    }
    bHint.textContent = parts.join(" · ");
  }

  function toggleBulk(open) {
    bulkOpen = open;
    bulkBox.style.display = bulkOpen ? "flex" : "none";
    bulkBtn.style.background = bulkOpen ? "rgba(124,156,255,.28)" : "rgba(255,255,255,.06)";
    if (bulkOpen) renderBulk();
    itpFit(node);
    itpDirty(node);
  }

  bulkBtn.addEventListener("click", () => toggleBulk(!bulkOpen));
  qInput.addEventListener("input", () => { bulkQuery = qInput.value; renderBulk(); });
  allMatchBtn.addEventListener("click", () => {
    const q = bulkQuery.trim().toLowerCase();
    const matched = q ? allFiles.filter((f) => f.toLowerCase().indexOf(q) >= 0) : allFiles;
    for (const f of matched) bulkSel.add(f);
    renderBulk();
  });
  addSelBtn.addEventListener("click", () => {
    if (!bulkSel.size) return;
    const dw = node.widgets.find((x) => x.name === "默认强度");
    const st = (dw && typeof dw.value === "number") ? dw.value : 1.0;
    let added = 0;
    bulkSel.forEach((f) => {
      if (!items.some((x) => x.lora === f)) {
        items.push({ lora: f, on: true, strength: st });
        added++;
      }
    });
    bulkSel.clear();
    bulkQuery = "";
    qInput.value = "";
    toggleBulk(false);
    render();
    console.log(`[GULU工具箱] 批量添加了 ${added} 个 LoRA`);
  });

  function render() {
    rowsBox.innerHTML = "";
    if (!items.length) {
      rowsBox.appendChild(itpDiv(
        "font-size:11px;opacity:.45;text-align:center;padding:6px 0;",
        "还没有 LoRA，用上面的下拉添加"
      ));
      save();
      return;
    }

    items.forEach((it, i) => {
      const row = itpDiv(
        "display:flex;align-items:center;gap:4px;height:" + (ITP_LORA_ROW_H - 5) + "px;"
      );

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = it.on;
      cb.style.cssText = "flex:none;margin:0;cursor:pointer;accent-color:#7c9cff;";
      cb.addEventListener("change", () => { it.on = cb.checked; render(); });

      const base = it.lora.split(/[\\/]/).pop();
      const nm = itpDiv(
        "flex:1;min-width:0;font-size:11px;white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis;opacity:" + (it.on ? "1" : ".42") + ";",
        base
      );
      nm.title = it.lora;

      const dec = itpBtn("◀", "width:18px;min-width:18px;padding:0;");
      const val = itpDiv(
        "flex:none;width:36px;text-align:center;font-size:11px;opacity:" + (it.on ? ".85" : ".42") + ";",
        it.strength.toFixed(2)
      );
      const inc = itpBtn("▶", "width:18px;min-width:18px;padding:0;");
      dec.addEventListener("click", () => {
        it.strength = Math.round((it.strength - 0.05) * 100) / 100;
        render();
      });
      inc.addEventListener("click", () => {
        it.strength = Math.round((it.strength + 0.05) * 100) / 100;
        render();
      });

      const del = itpBtn("×", "width:18px;min-width:18px;padding:0;opacity:.7;");
      del.addEventListener("click", () => { items.splice(i, 1); render(); });

      row.appendChild(cb);
      row.appendChild(nm);
      row.appendChild(dec);
      row.appendChild(val);
      row.appendChild(inc);
      row.appendChild(del);
      rowsBox.appendChild(row);
    });

    save();
  }

  itpLoraFiles().then((files) => {
    allFiles = files.slice();
    sel.innerHTML = "";
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "选择要添加的 LoRA（" + files.length + " 个）";
    sel.appendChild(ph);
    for (const f of files) {
      const o = document.createElement("option");
      o.value = f;
      o.textContent = f;
      sel.appendChild(o);
    }
    render();
  });

  addBtn.addEventListener("click", () => {
    const v = sel.value;
    if (!v) return;
    if (!items.some((x) => x.lora === v)) {
      const dw = node.widgets.find((x) => x.name === "默认强度");
      const st = (dw && typeof dw.value === "number") ? dw.value : 1.0;
      items.push({ lora: v, on: true, strength: st });
    }
    sel.value = "";
    render();
  });

  node.__guluLoraEditor = { items, render };

  setTimeout(() => { render(); itpFit(node); itpDirty(node); }, 0);
}

/* ==================================================================
 * LoRA 轮转面板（显示第几个 / 当前 LoRA / 强度）
 * ================================================================== */

const ITP_LORA_PANEL_H = 62;

function itpBuildLoraPanel(node) {
  const wrap = itpDiv(
    "display:flex;flex-direction:column;justify-content:center;gap:2px;" +
    "padding:6px 8px 8px;box-sizing:border-box;color:inherit;" +
    "font-family:system-ui,'Segoe UI','Microsoft YaHei',sans-serif;"
  );
  const l1 = itpDiv(
    "font-size:16px;font-weight:600;line-height:1.2;text-align:center;" +
    "letter-spacing:.5px;color:inherit;", "— / —"
  );
  const l2 = itpDiv(
    "font-size:11px;line-height:1.4;text-align:center;opacity:.72;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;width:100%;", "等待运行"
  );
  wrap.appendChild(l1);
  wrap.appendChild(l2);

  const widget = node.addDOMWidget("轮转进度", "gulu_lora_panel", wrap, {
    serialize: false,
    hideOnZoom: false,
  });
  widget.computeSize = function (width) {
    return [Math.max(140, width || 240), ITP_LORA_PANEL_H];
  };

  node.__guluLoraUI = { l1, l2 };
}

function itpUpdateLoraPanel(node, message) {
  const ui = node.__guluLoraUI;
  if (!ui) return;
  const list = message && message.gulu_show;
  const item = Array.isArray(list) ? list[0] : null;
  if (!item) return;

  ui.l1.textContent = `${item.index + 1} / ${item.total}`;
  const name = item.name || item.lora || "";
  const st = (typeof item.strength === "number") ? item.strength.toFixed(2) : "—";
  ui.l2.textContent = `${name}  ·  强度 ${st}`;
  ui.l2.title = item.lora || name;
  itpDirty(node);
}

/* ---------- 提示词批量导入：文字面板（没有图，只显示进度和预览） ---------- */

const ITP_PROMPT_PANEL_H = 66;

function itpBuildPromptPanel(node) {
  const wrap = itpDiv(
    "display:flex;flex-direction:column;justify-content:center;gap:3px;" +
    "padding:6px 8px 8px;box-sizing:border-box;color:inherit;" +
    "font-family:system-ui,'Segoe UI','Microsoft YaHei',sans-serif;"
  );
  const l1 = itpDiv(
    "font-size:15px;font-weight:600;line-height:1.2;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;", "— / —"
  );
  const l2 = itpDiv(
    "font-size:11px;line-height:1.35;opacity:.7;color:inherit;" +
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;", "等待运行"
  );
  const l3 = itpDiv(
    "font-size:10px;line-height:1.35;opacity:.5;color:inherit;" +
    "display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;", ""
  );
  wrap.appendChild(l1);
  wrap.appendChild(l2);
  wrap.appendChild(l3);

  const widget = node.addDOMWidget("提示词信息", "gulu_prompt_panel", wrap, {
    serialize: false,
    hideOnZoom: false,
  });
  widget.computeSize = function (width) {
    return [Math.max(140, width || 240), ITP_PROMPT_PANEL_H];
  };

  node.__guluPromptUI = { l1, l2, l3 };
}

function itpUpdatePromptPanel(node, message) {
  const ui = node.__guluPromptUI;
  if (!ui) return;
  const list = message && message.gulu_show;
  const item = Array.isArray(list) ? list[0] : null;
  if (!item) return;

  ui.l1.textContent = `${item.index + 1} / ${item.total}`;
  ui.l2.textContent = `${item.name || ""}  ·  ${item.chars || 0} 字`;
  ui.l2.title = item.path || item.name || "";
  ui.l3.textContent = item.preview || "";
  ui.l3.title = item.preview || "";
  itpDirty(node);
}

/* ==================================================================
 * 循环推进（批量导入 / LoRA 轮转 专用）
 * ------------------------------------------------------------------
 * ComfyUI 的图是 DAG，线不能接回自己形成环（会被 dependency_cycle 拒绝），
 * 所以「这一轮结束了」这个信号不能靠接线，而是用框架自己的事件：
 *
 *   executed           —— 某个节点跑完了（太早！下游可能还在采样）
 *   execution_success  —— 整轮工作流全部跑完（正确时机）
 *   execution_error / execution_interrupted —— 出错或取消，停止推进
 *
 * 所以流程是：loader 跑完先记住「还有下一张」→ 等整轮 success →
 * 才把「循环位置」+1 并提交下一轮。复杂工作流里也绝不会提前抢跑。
 * ================================================================== */

/* 同一轮里可能有好几个循环节点（图像批量 + 提示词批量 + LoRA 轮转）。
 * 所以这里必须按 nodeId 分别收集，不能用单个变量 —— 否则后执行的那个会把前一个冲掉，
 * 表现就是「只有一路在推进，另一路永远卡在第 1 条」。 */
const ITP_LOOP_PENDING = new Map();   // nodeId -> { nodeId, promptId, info }

/* 控制台日志里给节点起个人能看懂的名字 */
const ITP_LOOP_LABEL = {
  ImageBatchLoopLoader: "图像批量导入",
  PromptBatchLoopLoader: "提示词批量导入",
  LoRACycleCN: "LoRA 轮转",
};
let ITP_QUEUE_WRAPPED = false;
let ITP_EVENTS_HOOKED = false;

function itpLoopNodes(app) {
  const g = app && app.graph;
  return ((g && g._nodes) || []).filter((n) => {
    const c = ITP_NODES[n.type];
    return !!(c && c.loop);
  });
}

function itpSetLoopIndex(node, value) {
  const w = node.widgets && node.widgets.find((x) => x.name === "循环位置");
  if (w && typeof value === "number" && isFinite(value)) w.value = value;
}

function itpFindLoopNode(app, nodeId) {
  const g = app && app.graph;
  if (!g) return null;
  let n = null;
  if (g.getNodeById) {
    n = g.getNodeById(nodeId) || g.getNodeById(Number(nodeId));
  }
  if (!n) {
    n = (g._nodes || []).find((x) => String(x.id) === String(nodeId));
  }
  const c = n ? ITP_NODES[n.type] : null;
  return c && c.loop ? n : null;
}

/** 包一层 queuePrompt：手动点运行时把所有循环节点归零，从第一张重新开始 */
function itpWrapQueue(app) {
  if (ITP_QUEUE_WRAPPED || !app || typeof app.queuePrompt !== "function") return;
  ITP_QUEUE_WRAPPED = true;
  const orig = app.queuePrompt;
  app.queuePrompt = function () {
    try {
      if (!window.__guluAutoQueue) {
        ITP_LOOP_PENDING.clear();
        for (const n of itpLoopNodes(app)) itpSetLoopIndex(n, 0);
      }
    } catch (e) { /* 忽略 */ }
    window.__guluAutoQueue = false;
    return orig.apply(this, arguments);
  };
}

/**
 * 整轮真的跑完了，才推进。items = 这一轮所有报告了 gulu_show 的循环节点。
 *
 * 两条规则：
 *   · 每个节点各自 +1 —— 所以「第 N 张图」天然配「第 N 条提示词」；
 *   · 只要有一个节点报告 more = false，整批立刻结束（按较短的那一批收尾）。
 *     不这么做的话，短的那路会被推超、后端绕回第 1 条 → 无限出图。
 */
function itpApplyPending(app, items) {
  let needQueue = false;
  let anyDone = false;

  for (const it of items) {
    const node = itpFindLoopNode(app, it.nodeId);
    if (!node) continue;
    const info = it.info || {};
    const label = ITP_LOOP_LABEL[node.type] || "批量节点";

    if (!info.more) {
      itpSetLoopIndex(node, 0);
      itpDirty(node);
      anyDone = true;
      console.log(`[GULU工具箱] ${label} 完成：共 ${info.total} 条`);
      continue;
    }

    // next 优先用后端给的；老版本没这个字段就用 index + 1 兜底
    const nextIdx = (typeof info.next === "number") ? info.next : (Number(info.index) + 1);
    itpSetLoopIndex(node, nextIdx);
    itpDirty(node);
    needQueue = true;
    console.log(`[GULU工具箱] ${label} ${info.index + 1}/${info.total} 完成 → 推进到第 ${nextIdx + 1} 个`);
  }

  if (anyDone || !needQueue) {
    // 有一路跑完 = 这一批结束；所有循环节点一起复位，免得下次手动运行从中间开始
    for (const n of itpLoopNodes(app)) itpSetLoopIndex(n, 0);
    window.__guluAutoQueue = false;
    if (anyDone) console.log("[GULU工具箱] 有一路已跑完，本批结束（按较短的那批收尾）");
    return;
  }

  window.__guluAutoQueue = true;
  setTimeout(() => {
    try {
      const p = app.queuePrompt(0, 1);
      if (p && typeof p.catch === "function") p.catch(() => {});
    } catch (e) {
      window.__guluAutoQueue = false;
      console.warn("[GULU工具箱] 自动循环中断：", e);
    }
  }, 120);
}

/** 挂框架事件：记待推进的轮次、整轮成功才推进、出错就停 */
function itpHookLoopEvents(app) {
  if (ITP_EVENTS_HOOKED) return;
  const api = app && app.api;
  if (!api || typeof api.addEventListener !== "function") return;
  ITP_EVENTS_HOOKED = true;

  api.addEventListener("executed", (e) => {
    const d = e && e.detail;
    if (!d || !d.output) return;
    const list = d.output.gulu_show;
    const info = Array.isArray(list) ? list[0] : null;
    if (!info) return;
    // 「一次模式」不循环；没有 mode 字段的老节点默认按循环处理
    if (info.mode && info.mode !== "loop") return;

    const nodeId = String(d.display_node != null ? d.display_node : d.node);
    ITP_LOOP_PENDING.set(nodeId, { nodeId, promptId: d.prompt_id, info });
  });

  api.addEventListener("execution_success", (e) => {
    const pid = e && e.detail && e.detail.prompt_id;
    if (ITP_LOOP_PENDING.size === 0) return;

    const items = [];
    for (const it of ITP_LOOP_PENDING.values()) {
      // 只认这一轮（prompt_id 对得上）的报告，避免上一轮的残留混进来
      if (!pid || !it.promptId || it.promptId === pid) items.push(it);
    }
    ITP_LOOP_PENDING.clear();
    if (!items.length) return;

    itpApplyPending(app, items);
  });

  const stop = () => { ITP_LOOP_PENDING.clear(); };
  api.addEventListener("execution_error", stop);
  api.addEventListener("execution_interrupted", stop);
}

/* ==================================================================
 * 注册
 * ================================================================== */

if (ITP_APP && typeof ITP_APP.registerExtension === "function") {
  ITP_APP.registerExtension({
    name: "GULUToolbox.WidgetVisibility",

    beforeRegisterNodeDef(nodeType, nodeData) {
      const conf = nodeData ? ITP_NODES[nodeData.name] : null;
      if (!conf) return;
      const rules = conf.rules;
      const hidden = conf.hide || [];

      // ---------------- 创建时：装显隐联动
      const prevCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = prevCreated ? prevCreated.apply(this, arguments) : undefined;
        const node = this;
        try {
          const find = (name) =>
            node.widgets && node.widgets.find((w) => w.name === name);

          // 节点下方的小面板（按边压缩：显示计算后的宽高）
          if (conf.panel === "size") itpBuildSizePanel(node);
          if (conf.panel === "loader") itpBuildLoaderPanel(node);
          if (conf.panel === "prompt") itpBuildPromptPanel(node);
          if (conf.panel === "lora") { itpBuildLoraEditor(node); itpBuildLoraPanel(node); }
          if (conf.loop) { itpWrapQueue(itpApp()); itpHookLoopEvents(itpApp()); }

          // 内部参数：直接不显示
          for (const name of hidden) itpShow(find(name), false);

          for (const ctrlName of Object.keys(rules)) {
            const ctrl = find(ctrlName);
            if (!ctrl || ctrl.__itpHooked) continue;
            ctrl.__itpHooked = true;
            const prev = ctrl.callback;
            ctrl.callback = function () {
              const out = prev ? prev.apply(this, arguments) : undefined;
              itpRefresh(node, rules);
              itpFit(node);
              itpDirty(node);
              return out;
            };
          }

          itpRefresh(node, rules);
          setTimeout(() => { itpRefresh(node, rules); itpFit(node); itpDirty(node); }, 0);
        } catch (e) {
          console.warn("[GULU工具箱] 界面初始化失败（节点功能不受影响）：", e);
        }
        return r;
      };

      // ---------------- 执行完成：把计算出的宽高写进面板
      if (conf.panel) {
        const prevExec = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
          const r = prevExec ? prevExec.apply(this, arguments) : undefined;
          try {
            if (conf.panel === "size") itpUpdateSizePanel(this, message);
            if (conf.panel === "loader") itpUpdateLoaderPanel(this, message);
            if (conf.panel === "prompt") itpUpdatePromptPanel(this, message);
            if (conf.panel === "lora") itpUpdateLoraPanel(this, message);
          } catch (e) {
            console.warn("[GULU工具箱] 尺寸面板更新失败：", e);
          }
          return r;
        };
      }

      // ---------------- 从工作流载入：按保存的参数恢复显隐
      const prevCfg = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function () {
        const r = prevCfg ? prevCfg.apply(this, arguments) : undefined;
        const node = this;
        setTimeout(() => {
          try {
            const find = (n) => node.widgets && node.widgets.find((w) => w.name === n);
            for (const name of hidden) itpShow(find(name), false);
            itpRefresh(node, rules);
            itpFit(node);
            itpDirty(node);
          } catch (e) { /* 忽略 */ }
        }, 0);
        return r;
      };
    },
  });
} else {
  console.warn("[GULU工具箱] 未取得 ComfyUI 前端 app，跳过界面联动（节点功能不受影响）");
}
