import { GroupWorkbookController } from "./group-controller.js";

const bridge = window.AstrBotPluginPage;

const elements = {
  refreshButton: document.getElementById("refresh-button"),
  groupSelect: document.getElementById("group-select"),
  groupCount: document.getElementById("group-count"),
  groupDetails: document.getElementById("group-details"),
  remarkEditor: document.getElementById("remark-editor"),
  groupRemark: document.getElementById("group-remark"),
  saveRemarkButton: document.getElementById("save-remark-button"),
  manualUmo: document.getElementById("manual-umo"),
  manualRemark: document.getElementById("manual-remark"),
  addGroupButton: document.getElementById("add-group-button"),
  deleteGroupButton: document.getElementById("delete-group-button"),
  replaceField: document.getElementById("replace-field"),
  replaceSelect: document.getElementById("replace-select"),
  fileInput: document.getElementById("file-input"),
  uploadButton: document.getElementById("upload-button"),
  workbookCount: document.getElementById("workbook-count"),
  workbookList: document.getElementById("workbook-list"),
  confirmDialog: document.getElementById("confirm-dialog"),
  confirmTitle: document.getElementById("confirm-title"),
  confirmMessage: document.getElementById("confirm-message"),
  accountCount: document.getElementById("account-count"),
  accountUsername: document.getElementById("account-username"),
  accountRole: document.getElementById("account-role"),
  createAccountButton: document.getElementById("create-account-button"),
  accountList: document.getElementById("account-list"),
  createInviteButton: document.getElementById("create-invite-button"),
  inviteList: document.getElementById("invite-list"),
  feedback: document.getElementById("feedback"),
};

const state = {
  groups: [],
  workbooks: [],
  selectedGroupId: "",
  accounts: [],
  invites: [],
  busy: false,
};

const groupController = new GroupWorkbookController(state, {
  async listGroups() {
    return (await bridge.apiGet("groups")).groups;
  },
  async listWorkbooks(bindingId) {
    return (await bridge.apiGet("tables", { group_id: bindingId })).workbooks;
  },
  importWorkbook(bindingId, file) {
    return bridge.upload(`tables/import/${bindingId}`, file);
  },
  replaceWorkbook(workbookId, file) {
    return bridge.upload(`tables/${workbookId}/replace`, file);
  },
  deleteWorkbook(workbookId) {
    return bridge.apiPost(`tables/${workbookId}/delete`, {});
  },
});

function setBusy(busy) {
  state.busy = busy;
  elements.refreshButton.disabled = busy;
  elements.groupSelect.disabled = busy;
  elements.groupRemark.disabled = busy;
  elements.saveRemarkButton.disabled = busy || !state.selectedGroupId;
  elements.manualUmo.disabled = busy;
  elements.manualRemark.disabled = busy;
  elements.addGroupButton.disabled = busy;
  elements.deleteGroupButton.disabled = busy || !state.selectedGroupId;
  elements.uploadButton.disabled = busy || !state.selectedGroupId;
  elements.accountUsername.disabled = busy;
  elements.accountRole.disabled = busy;
  elements.createAccountButton.disabled = busy;
  elements.createInviteButton.disabled = busy;
  elements.uploadButton.textContent = busy ? "处理中…" : "开始导入";
}

function showFeedback(message, type = "success") {
  elements.feedback.textContent = message;
  elements.feedback.className = `feedback ${type}`;
  window.clearTimeout(showFeedback.timer);
  showFeedback.timer = window.setTimeout(() => {
    elements.feedback.className = "feedback hidden";
  }, 6000);
}

function selectedGroup() {
  return state.groups.find((group) => group.id === state.selectedGroupId);
}

function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function confirmAction(message, title = "确认删除") {
  return new Promise((resolve) => {
    elements.confirmTitle.textContent = title;
    elements.confirmMessage.textContent = message;
    elements.confirmDialog.returnValue = "cancel";
    elements.confirmDialog.addEventListener(
      "close",
      () => resolve(elements.confirmDialog.returnValue === "confirm"),
      { once: true },
    );
    elements.confirmDialog.showModal();
  });
}

function appendDetail(term, description) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = description;
  elements.groupDetails.append(dt, dd);
}

function renderGroups() {
  const previous = state.selectedGroupId;
  elements.groupSelect.replaceChildren();

  if (state.groups.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无已登记群";
    elements.groupSelect.append(option);
    state.selectedGroupId = "";
  } else {
    for (const group of state.groups) {
      const option = document.createElement("option");
      option.value = group.id;
      option.textContent = group.remark
        ? `${group.remark} · ${group.group_name} · ${group.group_id}`
        : `${group.group_name} · ${group.group_id}`;
      elements.groupSelect.append(option);
    }
    state.selectedGroupId = state.groups.some((group) => group.id === previous)
      ? previous
      : state.groups[0].id;
    elements.groupSelect.value = state.selectedGroupId;
  }

  elements.groupCount.textContent = String(state.groups.length);
  elements.groupDetails.replaceChildren();
  const group = selectedGroup();
  elements.remarkEditor.classList.toggle("hidden", !group);
  if (group) {
    elements.groupRemark.value = group.remark ?? "";
    appendDetail("平台", group.platform_id);
    appendDetail("群号", group.group_id);
    appendDetail("UMO", group.umo);
    if (group.remark) appendDetail("备注", group.remark);
    appendDetail(
      "数据",
      `${group.workbook_count} 份工作簿 · ${group.support_count} 条助战`,
    );
  } else {
    elements.groupRemark.value = "";
  }
  elements.deleteGroupButton.disabled = state.busy || !group;
  elements.saveRemarkButton.disabled = state.busy || !group;
}

function createMetric(label, value) {
  const metric = document.createElement("span");
  metric.className = "metric";
  const strong = document.createElement("strong");
  strong.textContent = String(value);
  const small = document.createElement("small");
  small.textContent = label;
  metric.append(strong, small);
  return metric;
}

function renderWorkbooks() {
  elements.workbookList.replaceChildren();
  elements.replaceSelect.replaceChildren();
  elements.workbookCount.textContent = String(state.workbooks.length);

  if (!state.selectedGroupId) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "请选择一个群。";
    elements.workbookList.append(empty);
    return;
  }

  if (state.workbooks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "这个群还没有导入工作簿。";
    elements.workbookList.append(empty);
  }

  for (const workbook of state.workbooks) {
    const option = document.createElement("option");
    option.value = workbook.id;
    option.textContent = workbook.original_filename;
    elements.replaceSelect.append(option);

    const item = document.createElement("article");
    item.className = "workbook-item";

    const main = document.createElement("div");
    main.className = "workbook-main";
    const name = document.createElement("h3");
    name.textContent = workbook.original_filename;
    const meta = document.createElement("p");
    const importedAt = new Date(workbook.imported_at);
    const timestamp = Number.isNaN(importedAt.getTime())
      ? workbook.imported_at
      : importedAt.toLocaleString();
    meta.textContent = `${workbook.sheets.join(" · ")} · ${timestamp}`;
    main.append(name, meta);

    const metrics = document.createElement("div");
    metrics.className = "metrics";
    metrics.append(
      createMetric("干员", workbook.operator_count),
      createMetric("助战", workbook.support_count),
      createMetric("警告", workbook.warning_count),
    );

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button danger ghost compact";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteWorkbook(workbook));

    item.append(main, metrics, remove);
    elements.workbookList.append(item);
  }

  const replaceMode = currentMode() === "replace";
  elements.replaceField.classList.toggle("hidden", !replaceMode);
  elements.uploadButton.disabled =
    state.busy ||
    !state.selectedGroupId ||
    (replaceMode && state.workbooks.length === 0);
}

async function loadWorkbooks() {
  if (!state.selectedGroupId) {
    state.workbooks = [];
    renderWorkbooks();
    return;
  }
  await groupController.loadWorkbooks();
  renderWorkbooks();
}

async function refreshAll() {
  setBusy(true);
  try {
    await groupController.loadGroups();
    renderGroups();
    await loadWorkbooks();
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
    renderWorkbooks();
  }
}

function compactAction(label, action, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button ${danger ? "danger ghost" : "secondary"} compact`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function renderAccounts() {
  elements.accountList.replaceChildren();
  elements.accountCount.textContent = String(state.accounts.length);
  if (state.accounts.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无独立站点账号。";
    elements.accountList.append(empty);
    return;
  }
  for (const account of state.accounts) {
    const item = document.createElement("article");
    item.className = "workbook-item account-item";
    const main = document.createElement("div");
    main.className = "workbook-main";
    const name = document.createElement("h3");
    const meta = document.createElement("p");
    name.textContent = account.username;
    meta.textContent =
      `${account.role === "admin" ? "管理员" : "普通用户"} · ` +
      `${account.is_active ? "已启用" : "已禁用"}` +
      `${account.must_change_password ? " · 待修改临时密码" : ""}`;
    main.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "account-actions";
    actions.append(
      compactAction(
        account.is_active ? "禁用" : "启用",
        () => setAccountActive(account, !account.is_active),
        account.is_active,
      ),
      compactAction("重置密码", () => resetAccountPassword(account)),
      compactAction(
        account.role === "admin" ? "撤销管理员" : "设为管理员",
        () =>
          setAccountRole(
            account,
            account.role === "admin" ? "user" : "admin",
          ),
      ),
    );
    item.append(main, actions);
    elements.accountList.append(item);
  }
}

function renderInvites() {
  elements.inviteList.replaceChildren();
  const activeInvites = state.invites.filter(
    (invite) =>
      !invite.used_at &&
      !invite.revoked_at &&
      new Date(invite.expires_at).getTime() > Date.now(),
  );
  if (activeInvites.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无有效邀请码。";
    elements.inviteList.append(empty);
    return;
  }
  for (const invite of activeInvites) {
    const item = document.createElement("article");
    item.className = "workbook-item account-item";
    const main = document.createElement("div");
    main.className = "workbook-main";
    const name = document.createElement("h3");
    const meta = document.createElement("p");
    name.textContent = invite.creator_username;
    meta.textContent = `过期：${new Date(invite.expires_at).toLocaleString()}`;
    main.append(name, meta);
    item.append(
      main,
      compactAction("撤销", () => revokeInvite(invite), true),
    );
    elements.inviteList.append(item);
  }
}

async function loadSuperAdminData() {
  const [accountResult, inviteResult] = await Promise.all([
    bridge.apiGet("accounts"),
    bridge.apiGet("invites"),
  ]);
  state.accounts = accountResult.users ?? [];
  state.invites = inviteResult.invites ?? [];
  renderAccounts();
  renderInvites();
}

async function createAccount() {
  const username = elements.accountUsername.value.trim();
  if (!username) {
    showFeedback("请输入用户名。", "error");
    return;
  }
  setBusy(true);
  try {
    const result = await bridge.apiPost("accounts/create", {
      username,
      role: elements.accountRole.value,
    });
    elements.accountUsername.value = "";
    showFeedback(
      `账号已创建，临时密码（仅显示一次）：${result.temporary_password}`,
    );
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function setAccountActive(account, active) {
  setBusy(true);
  try {
    await bridge.apiPost(`accounts/${account.id}/active`, { active });
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function resetAccountPassword(account) {
  if (!(await confirmAction(`重置“${account.username}”的密码吗？`))) return;
  setBusy(true);
  try {
    const result = await bridge.apiPost(
      `accounts/${account.id}/reset-password`,
      {},
    );
    showFeedback(`临时密码（仅显示一次）：${result.temporary_password}`);
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function setAccountRole(account, role) {
  const label = role === "admin" ? "授予管理员权限" : "撤销管理员权限";
  if (!(await confirmAction(`确定为“${account.username}”${label}吗？`, label))) {
    return;
  }
  setBusy(true);
  try {
    await bridge.apiPost(`accounts/${account.id}/role`, { role });
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function createInvite() {
  setBusy(true);
  try {
    const result = await bridge.apiPost("invites", {});
    showFeedback(`邀请码（仅显示一次，7 天有效）：${result.code}`);
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function revokeInvite(invite) {
  setBusy(true);
  try {
    await bridge.apiPost(`invites/${invite.id}/delete`, {});
    await loadSuperAdminData();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function uploadWorkbook() {
  const file = elements.fileInput.files[0];
  if (!state.selectedGroupId) {
    showFeedback("请先选择一个群。", "error");
    return;
  }
  if (!file) {
    showFeedback("请选择 .xlsx 文件。", "error");
    return;
  }

  const mode = currentMode();
  const replacementId = elements.replaceSelect.value;
  if (mode === "replace" && !replacementId) {
    showFeedback("请选择要替换的工作簿。", "error");
    return;
  }
  setBusy(true);
  try {
    const result = await groupController.upload(mode, file, replacementId);
    const warningText =
      result.warnings?.length > 0 ? `，${result.warnings.length} 条警告` : "";
    showFeedback(
      `导入完成：${result.operator_count} 个干员，${result.support_count} 条助战${warningText}。`,
    );
    elements.fileInput.value = "";
    await refreshAll();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
    renderWorkbooks();
  }
}

async function addManualGroup() {
  const umo = elements.manualUmo.value.trim();
  const remark = elements.manualRemark.value.trim();
  if (!umo) {
    showFeedback("请输入群聊 UMO。", "error");
    return;
  }

  setBusy(true);
  try {
    const result = await bridge.apiPost("groups/manual", { umo, remark });
    state.selectedGroupId = result.group.id;
    elements.manualUmo.value = "";
    elements.manualRemark.value = "";
    showFeedback("UMO 已添加。");
    await refreshAll();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function saveGroupRemark() {
  const group = selectedGroup();
  if (!group) return;

  setBusy(true);
  try {
    await bridge.apiPost(`groups/${group.id}/remark`, {
      remark: elements.groupRemark.value.trim(),
    });
    showFeedback("UMO 备注已保存。");
    await refreshAll();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function deleteWorkbook(workbook) {
  const confirmed = await confirmAction(
    `确定删除“${workbook.original_filename}”吗？对应查询数据也会删除。`,
  );
  if (!confirmed) return;

  setBusy(true);
  try {
    await groupController.deleteWorkbook(workbook.id);
    showFeedback("工作簿已删除。");
    await refreshAll();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
    renderWorkbooks();
  }
}

async function deleteGroup() {
  const group = selectedGroup();
  if (!group) return;
  const confirmed = await confirmAction(
    `确定删除“${group.group_name}”及其 ${group.workbook_count} 份工作簿吗？此操作不可撤销。`,
  );
  if (!confirmed) return;

  setBusy(true);
  try {
    await bridge.apiPost(`groups/${group.id}/delete`, {});
    state.selectedGroupId = "";
    showFeedback("群及其助战数据已删除。");
    await refreshAll();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
  }
}

await bridge.ready();
elements.refreshButton.addEventListener("click", refreshAll);
elements.groupSelect.addEventListener("change", async (event) => {
  groupController.selectGroup(event.target.value);
  renderGroups();
  setBusy(true);
  try {
    await loadWorkbooks();
  } catch (error) {
    showFeedback(error.message, "error");
  } finally {
    setBusy(false);
    renderWorkbooks();
  }
});
elements.uploadButton.addEventListener("click", uploadWorkbook);
elements.addGroupButton.addEventListener("click", addManualGroup);
elements.saveRemarkButton.addEventListener("click", saveGroupRemark);
elements.deleteGroupButton.addEventListener("click", deleteGroup);
elements.createAccountButton.addEventListener("click", createAccount);
elements.createInviteButton.addEventListener("click", createInvite);
for (const radio of document.querySelectorAll('input[name="mode"]')) {
  radio.addEventListener("change", renderWorkbooks);
}

await refreshAll();
