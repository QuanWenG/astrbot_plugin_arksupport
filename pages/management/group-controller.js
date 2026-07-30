/** Shared group/workbook state controller for both web transports. */
export class GroupWorkbookController {
  constructor(state, transport) {
    this.state = state;
    this.transport = transport;
  }

  async loadGroups() {
    this.state.groups = (await this.transport.listGroups()) ?? [];
    if (!this.state.groups.some((group) => group.id === this.state.selectedGroupId)) {
      this.state.selectedGroupId = this.state.groups[0]?.id ?? "";
    }
    return this.state.groups;
  }

  async loadWorkbooks() {
    this.state.workbooks = this.state.selectedGroupId
      ? ((await this.transport.listWorkbooks(this.state.selectedGroupId)) ?? [])
      : [];
    return this.state.workbooks;
  }

  selectGroup(bindingId) {
    this.state.selectedGroupId = bindingId || "";
  }

  async upload(mode, file, replacementId) {
    if (mode === "replace") {
      return this.transport.replaceWorkbook(replacementId, file);
    }
    return this.transport.importWorkbook(this.state.selectedGroupId, file);
  }

  async deleteWorkbook(workbookId) {
    return this.transport.deleteWorkbook(workbookId);
  }
}
