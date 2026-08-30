// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

import CompanyManagement from "./CompanyManagement.vue";

const mocks = vi.hoisted(() => ({
  listFineJobCompanies: vi.fn(),
  createFineJobCompany: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn()
}));

vi.mock("@/services/api", () => ({
  api: {
    listFineJobCompanies: mocks.listFineJobCompanies,
    createFineJobCompany: mocks.createFineJobCompany,
    updateFineJobCompany: vi.fn(),
    addFineJobCompanyAlias: vi.fn(),
    deleteFineJobCompanyAlias: vi.fn(),
    setFineJobCompanyBlacklist: vi.fn()
  }
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    error: vi.fn(),
    success: mocks.messageSuccess,
    warning: mocks.messageWarning
  },
  ElMessageBox: {
    prompt: vi.fn()
  }
}));

const ElButtonStub = defineComponent({
  emits: ["click"],
  template: "<button type='button' v-bind='$attrs' @click='$emit(\"click\", $event)'><slot /></button>"
});

const ElInputStub = defineComponent({
  props: {
    modelValue: { type: String, default: "" }
  },
  emits: ["update:modelValue"],
  template: "<textarea v-bind='$attrs' :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)' />"
});

const ElDialogStub = defineComponent({
  template: "<div><slot /><slot name='footer' /></div>"
});

const SlotStub = defineComponent({ template: "<div><slot /></div>" });
const EmptyStub = defineComponent({ template: "<div />" });

describe("CompanyManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listFineJobCompanies.mockResolvedValue({ items: [], total: 0 });
    mocks.createFineJobCompany.mockResolvedValue({});
  });

  it("fills the requested example and imports normalized company names as outsourcing", async () => {
    const wrapper = mount(CompanyManagement, {
      global: {
        stubs: {
          ElButton: ElButtonStub,
          ElInput: ElInputStub,
          ElDialog: ElDialogStub,
          ElForm: SlotStub,
          ElFormItem: SlotStub,
          ElTable: SlotStub,
          ElTableColumn: EmptyStub,
          ElPagination: SlotStub,
          ElSelect: SlotStub,
          ElOption: SlotStub,
          ElTag: SlotStub,
          ElRadioGroup: SlotStub,
          ElRadioButton: SlotStub
        },
        directives: {
          loading: () => undefined
        }
      }
    });
    await flushPromises();

    await wrapper.get("button").trigger("click");
    await wrapper.get('[data-testid="fill-outsourcing-example"]').trigger("click");

    expect((wrapper.get('[data-testid="batch-outsourcing-input"]').element as HTMLTextAreaElement).value)
      .toContain("中软，软通");

    await wrapper.get('[data-testid="batch-outsourcing-input"]').setValue("中软，软通, 中软\n凯捷");
    await wrapper.get('[data-testid="batch-outsourcing-submit"]').trigger("click");
    await flushPromises();

    expect(mocks.createFineJobCompany).toHaveBeenCalledTimes(3);
    expect(mocks.createFineJobCompany).toHaveBeenNthCalledWith(1, { name: "中软", company_type: "outsourcing" });
    expect(mocks.createFineJobCompany).toHaveBeenNthCalledWith(2, { name: "软通", company_type: "outsourcing" });
    expect(mocks.createFineJobCompany).toHaveBeenNthCalledWith(3, { name: "凯捷", company_type: "outsourcing" });
    expect(mocks.messageSuccess).toHaveBeenCalledWith("已录入 3 家外包公司");
  });
});
