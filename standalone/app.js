import { GroupWorkbookController } from "/shared/group-controller.js";

const state = {
  user: null,
  csrf: "",
  groups: [],
  workbooks: [],
  selectedGroupId: "",
};

const groupController = new GroupWorkbookController(state, {
  async listGroups() {
    return (await api("/groups")).groups;
  },
  async listWorkbooks(bindingId) {
    return (await api(`/groups/${bindingId}/workbooks`)).workbooks;
  },
  importWorkbook(bindingId, file) {
    const form = new FormData();
    form.append("file", file);
    return api(`/groups/${bindingId}/workbooks`, { method: "POST", body: form });
  },
  replaceWorkbook(workbookId, file) {
    const form = new FormData();
    form.append("file", file);
    return api(`/workbooks/${workbookId}/replace`, { method: "POST", body: form });
  },
  deleteWorkbook(workbookId) {
    return api(`/workbooks/${workbookId}`, { method: "DELETE", body: {} });
  },
});

const $ = (id) => document.getElementById(id);
const elements = {
  authView: $("auth-view"),
  passwordView: $("password-view"),
  appView: $("app-view"),
  sessionActions: $("session-actions"),
  identity: $("identity"),
  groupSelect: $("group-select"),
  groupCount: $("group-count"),
  groupDetails: $("group-details"),
  remarkPanel: $("remark-panel"),
  groupRemark: $("group-remark"),
  removeGroup: $("remove-group"),
  replacePanel: $("replace-panel"),
  replaceSelect: $("replace-select"),
  workbookList: $("workbook-list"),
  workbookCount: $("workbook-count"),
  adminView: $("admin-view"),
  userList: $("user-list"),
  userCount: $("user-count"),
  inviteList: $("invite-list"),
  feedback: $("feedback"),
  dialog: $("dialog"),
  dialogTitle: $("dialog-title"),
  dialogMessage: $("dialog-message"),
};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.csrf && !["GET", "HEAD"].includes(options.method || "GET")) {
    headers.set("X-CSRF-Token", state.csrf);
  }
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api${path}`, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function feedback(message, error = false) {
  elements.feedback.textContent = message;
  elements.feedback.className = `feedback${error ? " error" : ""}`;
  clearTimeout(feedback.timer);
  feedback.timer = setTimeout(() => elements.feedback.classList.add("hidden"), 12000);
}

function confirmAction(message, title = "确认操作") {
  elements.dialogTitle.textContent = title;
  elements.dialogMessage.textContent = message;
  elements.dialog.returnValue = "cancel";
  elements.dialog.showModal();
  return new Promise((resolve) => {
    elements.dialog.addEventListener(
      "close",
      () => resolve(elements.dialog.returnValue === "confirm"),
      { once: true },
    );
  });
}

function showView() {
  const authenticated = Boolean(state.user);
  elements.authView.classList.toggle("hidden", authenticated);
  elements.sessionActions.classList.toggle("hidden", !authenticated);
  elements.passwordView.classList.toggle(
    "hidden",
    !authenticated || !state.user.must_change_password,
  );
  elements.appView.classList.toggle(
    "hidden",
    !authenticated || state.user.must_change_password,
  );
  if (authenticated) {
    elements.identity.textContent =
      `${state.user.username} · ${state.user.role === "admin" ? "管理员" : "用户"}`;
  }
}

function selectedGroup() {
  return state.groups.find((item) => item.id === state.selectedGroupId);
}

function detail(term, value) {
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value;
  elements.groupDetails.append(dt, dd);
}

function renderGroups() {
  const previous = state.selectedGroupId;
  elements.groupSelect.replaceChildren();
  for (const group of state.groups) {
    const option = document.createElement("option");
    option.value = group.id;
    option.textContent = group.remark
      ? `${group.remark} · ${group.group_name} · ${group.group_id}`
      : `${group.group_name} · ${group.group_id}`;
    elements.groupSelect.append(option);
  }
  state.selectedGroupId = state.groups.some((g) => g.id === previous)
    ? previous
    : state.groups[0]?.id || "";
  elements.groupSelect.value = state.selectedGroupId;
  elements.groupCount.textContent = String(state.groups.length);
  elements.groupDetails.replaceChildren();
  const group = selectedGroup();
  if (!group) {
    elements.groupSelect.append(new Option("暂无可访问群聊", ""));
    elements.remarkPanel.classList.add("hidden");
    elements.removeGroup.disabled = true;
    return;
  }
  detail("平台", group.platform_id);
  detail("群号", group.group_id);
  detail("UMO", group.umo);
  detail("数据", `${group.workbook_count} 份工作簿 · ${group.support_count} 条助战`);
  elements.remarkPanel.classList.toggle("hidden", !group.is_linked);
  elements.groupRemark.value = group.remark || "";
  elements.removeGroup.disabled = false;
  elements.removeGroup.textContent =
    state.user.role === "admin" ? "永久删除群及全部数据" : "移除我的群聊关联";
}

function itemButton(label, action, danger = false) {
  const button = document.createElement("button");
  button.className = `button ${danger ? "danger" : "secondary"}`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function renderWorkbooks() {
  elements.workbookList.replaceChildren();
  elements.replaceSelect.replaceChildren();
  elements.workbookCount.textContent = String(state.workbooks.length);
  if (!state.selectedGroupId || !state.workbooks.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.selectedGroupId ? "该群暂无工作簿。" : "请先选择群聊。";
    elements.workbookList.append(empty);
  }
  for (const workbook of state.workbooks) {
    elements.replaceSelect.append(new Option(workbook.original_filename, workbook.id));
    const article = document.createElement("article");
    article.className = "item";
    const main = document.createElement("div");
    main.className = "item-main";
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    title.textContent = workbook.original_filename;
    meta.className = "meta";
    meta.textContent =
      `${workbook.sheets.join(" · ")} · ${new Date(workbook.imported_at).toLocaleString()} · ` +
      `${workbook.operator_count} 干员 / ${workbook.support_count} 助战`;
    main.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "item-actions";
    actions.append(itemButton("删除", () => deleteWorkbook(workbook), true));
    article.append(main, actions);
    elements.workbookList.append(article);
  }
  const replace = document.querySelector('input[name="mode"]:checked').value === "replace";
  elements.replacePanel.classList.toggle("hidden", !replace);
  $("upload-button").disabled =
    !state.selectedGroupId || (replace && state.workbooks.length === 0);
}

async function loadGroups() {
  await groupController.loadGroups();
  renderGroups();
  await loadWorkbooks();
}

async function loadWorkbooks() {
  if (!state.selectedGroupId) {
    state.workbooks = [];
  } else {
    await groupController.loadWorkbooks();
  }
  renderWorkbooks();
}

async function refreshAdmin() {
  if (state.user.role !== "admin") return;
  elements.adminView.classList.remove("hidden");
  const [usersResult, invitesResult] = await Promise.all([
    api("/admin/users"),
    api("/admin/invites"),
  ]);
  renderUsers(usersResult.users || []);
  renderInvites(invitesResult.invites || []);
}

function renderUsers(users) {
  elements.userList.replaceChildren();
  elements.userCount.textContent = String(users.length);
  for (const user of users) {
    const item = document.createElement("div");
    item.className = "item";
    const main = document.createElement("div");
    main.className = "item-main";
    main.innerHTML = `<h3></h3><p class="meta"></p>`;
    main.querySelector("h3").textContent = user.username;
    main.querySelector("p").textContent =
      `${user.is_active ? "已启用" : "已禁用"}${user.must_change_password ? " · 待修改临时密码" : ""}`;
    const actions = document.createElement("div");
    actions.className = "item-actions";
    actions.append(
      itemButton(
        user.is_active ? "禁用" : "启用",
        () => setUserActive(user, !user.is_active),
        user.is_active,
      ),
      itemButton("重置密码", () => resetPassword(user)),
    );
    item.append(main, actions);
    elements.userList.append(item);
  }
}

function renderInvites(invites) {
  elements.inviteList.replaceChildren();
  const active = invites.filter(
    (i) =>
      !i.used_at &&
      !i.revoked_at &&
      new Date(i.expires_at).getTime() > Date.now(),
  );
  if (!active.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无有效邀请码。";
    elements.inviteList.append(empty);
  }
  for (const invite of active) {
    const item = document.createElement("div");
    item.className = "item";
    const main = document.createElement("div");
    main.className = "item-main";
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    title.textContent = "有效邀请码";
    meta.className = "meta";
    meta.textContent = `过期：${new Date(invite.expires_at).toLocaleString()}`;
    main.append(title, meta);
    item.append(
      main,
      itemButton("撤销", () => revokeInvite(invite), true),
    );
    elements.inviteList.append(item);
  }
}

async function initialize() {
  try {
    const result = await api("/auth/me");
    state.user = result.user;
    state.csrf = result.csrf_token;
    showView();
    if (!state.user.must_change_password) {
      await Promise.all([loadGroups(), refreshAdmin()]);
    }
  } catch {
    state.user = null;
    state.csrf = "";
    showView();
  }
}

document.querySelectorAll("[data-auth-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-auth-tab]").forEach((tab) =>
      tab.classList.toggle("active", tab === button));
    $("login-form").classList.toggle("hidden", button.dataset.authTab !== "login");
    $("register-form").classList.toggle("hidden", button.dataset.authTab !== "register");
  });
});

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/auth/login", {
      method: "POST",
      body: {
        username: $("login-username").value,
        password: $("login-password").value,
      },
    });
    state.user = result.user;
    state.csrf = result.csrf_token;
    showView();
    if (!state.user.must_change_password) {
      await Promise.all([loadGroups(), refreshAdmin()]);
    }
  } catch (error) { feedback(error.message, true); }
});

$("register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/auth/register", {
      method: "POST",
      body: {
        invite_code: $("invite-code").value,
        username: $("register-username").value,
        password: $("register-password").value,
      },
    });
    feedback("注册成功，请登录。");
    document.querySelector('[data-auth-tab="login"]').click();
  } catch (error) { feedback(error.message, true); }
});

$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/auth/change-password", {
      method: "POST",
      body: {
        current_password: $("current-password").value,
        new_password: $("new-password").value,
      },
    });
    state.user.must_change_password = false;
    state.csrf = result.csrf_token;
    showView();
    await Promise.all([loadGroups(), refreshAdmin()]);
  } catch (error) { feedback(error.message, true); }
});

$("logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST", body: {} }); } catch {}
  location.reload();
});

elements.groupSelect.addEventListener("change", async (event) => {
  groupController.selectGroup(event.target.value);
  renderGroups();
  await loadWorkbooks();
});

$("link-group").addEventListener("click", async () => {
  try {
    const result = await api("/groups/link", {
      method: "POST",
      body: { umo: $("manual-umo").value, remark: $("manual-remark").value },
    });
    state.selectedGroupId = result.group_id;
    $("manual-umo").value = "";
    $("manual-remark").value = "";
    await loadGroups();
    feedback("群聊已关联。");
  } catch (error) { feedback(error.message, true); }
});

$("save-remark").addEventListener("click", async () => {
  try {
    await api(`/groups/${state.selectedGroupId}/remark`, {
      method: "PATCH",
      body: { remark: elements.groupRemark.value },
    });
    await loadGroups();
    feedback("私有备注已保存。");
  } catch (error) { feedback(error.message, true); }
});

elements.removeGroup.addEventListener("click", async () => {
  const group = selectedGroup();
  if (!group) return;
  const isAdmin = state.user.role === "admin";
  const confirmed = await confirmAction(
    isAdmin
      ? `永久删除“${group.group_name}”及全部共享工作簿？`
      : `移除你对“${group.group_name}”的访问关联？`,
    isAdmin ? "永久删除群" : "移除关联",
  );
  if (!confirmed) return;
  try {
    await api(
      isAdmin ? `/groups/${group.id}` : `/groups/${group.id}/link`,
      { method: "DELETE", body: {} },
    );
    state.selectedGroupId = "";
    await loadGroups();
  } catch (error) { feedback(error.message, true); }
});

document.querySelectorAll('input[name="mode"]').forEach((radio) =>
  radio.addEventListener("change", renderWorkbooks));

$("upload-button").addEventListener("click", async () => {
  const file = $("file-input").files[0];
  if (!file) return feedback("请选择 .xlsx 文件。", true);
  const replace = document.querySelector('input[name="mode"]:checked').value === "replace";
  try {
    await groupController.upload(
      replace ? "replace" : "add",
      file,
      elements.replaceSelect.value,
    );
    $("file-input").value = "";
    await loadGroups();
    feedback("工作簿导入完成。");
  } catch (error) { feedback(error.message, true); }
});

async function deleteWorkbook(workbook) {
  if (!await confirmAction(`删除“${workbook.original_filename}”及其查询数据？`)) return;
  try {
    await groupController.deleteWorkbook(workbook.id);
    await loadGroups();
  } catch (error) { feedback(error.message, true); }
}

$("create-user").addEventListener("click", async () => {
  try {
    const result = await api("/admin/users", {
      method: "POST",
      body: { username: $("new-username").value },
    });
    $("new-username").value = "";
    feedback(`账号已创建。临时密码（仅显示一次）：\n${result.temporary_password}`);
    await refreshAdmin();
  } catch (error) { feedback(error.message, true); }
});

async function setUserActive(user, active) {
  try {
    await api(`/admin/users/${user.id}/active`, { method: "POST", body: { active } });
    await refreshAdmin();
  } catch (error) { feedback(error.message, true); }
}

async function resetPassword(user) {
  if (!await confirmAction(`为“${user.username}”生成新的临时密码？`)) return;
  try {
    const result = await api(`/admin/users/${user.id}/reset-password`, {
      method: "POST",
      body: {},
    });
    feedback(`密码已重置（仅显示一次）：\n${result.temporary_password}`);
    await refreshAdmin();
  } catch (error) { feedback(error.message, true); }
}

$("create-invite").addEventListener("click", async () => {
  try {
    const result = await api("/admin/invites", { method: "POST", body: {} });
    feedback(`邀请码（仅显示一次，7 天有效）：\n${result.code}`);
    await refreshAdmin();
  } catch (error) { feedback(error.message, true); }
});

async function revokeInvite(invite) {
  try {
    await api(`/admin/invites/${invite.id}`, { method: "DELETE", body: {} });
    await refreshAdmin();
  } catch (error) { feedback(error.message, true); }
}

await initialize();
