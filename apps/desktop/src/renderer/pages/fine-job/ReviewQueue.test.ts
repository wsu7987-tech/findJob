// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h, inject, provide } from "vue";

import { useFineJobWorkflowStore } from "@/stores/fineJobWorkflow";
import ReviewQueue from "./ReviewQueue.vue";

const radioGroupKey = Symbol("radio-group");

const mocks = vi.hoisted(() => ({
  listFineJobReviewItems: vi.fn(),
  listFineJobAutomationActions: vi.fn(),
  getFineJobBossExecutorStatus: vi.fn(),
  routerPush: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn()
}));

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {},
  NetworkError: class NetworkError extends Error {},
  api: {
    listFineJobReviewItems: mocks.listFineJobReviewItems,
    listFineJobAutomationActions: mocks.listFineJobAutomationActions,
    getFineJobBossExecutorStatus: mocks.getFineJobBossExecutorStatus
  }
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({
    push: mocks.routerPush
  })
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    error: mocks.messageError,
    success: mocks.messageSuccess,
    warning: vi.fn()
  },
  ElMessageBox: {
    confirm: vi.fn()
  }
}));

const ElRadioGroupStub = defineComponent({
  props: {
    modelValue: {
      type: String,
      default: ""
    }
  },
  emits: ["update:modelValue", "change"],
  setup(_props, { attrs, emit, slots }) {
    provide(radioGroupKey, (value: string) => {
      emit("update:modelValue", value);
      emit("change", value);
    });
    return () => h("div", attrs, slots.default?.());
  }
});

const ElRadioButtonStub = defineComponent({
  props: {
    value: {
      type: String,
      required: true
    }
  },
  setup(props, { slots }) {
    const changeStatus = inject<(value: string) => void>(radioGroupKey);
    return () =>
      h("button", {
        "data-value": props.value,
        type: "button",
        onClick: () => changeStatus?.(props.value)
      }, slots.default?.());
  }
});

const ElButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  template: "<button v-bind=\"$attrs\" type=\"button\" @click=\"$emit('click', $event)\"><slot /></button>"
});

const SlotStub = defineComponent({
  template: "<div><slot /></div>"
});

const EmptyStub = defineComponent({
  template: "<div />"
});

describe("ReviewQueue", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    mocks.listFineJobReviewItems.mockResolvedValue({ items: [], total: 0 });
    mocks.listFineJobAutomationActions.mockResolvedValue({ actions: [], total: 0 });
    mocks.getFineJobBossExecutorStatus.mockResolvedValue({
      executor: null,
      current_task: null,
      queue: {
        total: 0,
        actions: []
      }
    });
  });

  it("使用不依赖 TabPane 的状态控件切换待确认列表", async () => {
    const wrapper = mount(ReviewQueue, {
      global: {
        stubs: {
          ElAlert: SlotStub,
          ElButton: ElButtonStub,
          ElDatePicker: EmptyStub,
          ElDescriptions: SlotStub,
          ElDescriptionsItem: SlotStub,
          ElDrawer: SlotStub,
          ElForm: SlotStub,
          ElFormItem: SlotStub,
          ElInput: EmptyStub,
          ElLink: SlotStub,
          ElOption: EmptyStub,
          ElPagination: EmptyStub,
          ElRadioButton: ElRadioButtonStub,
          ElRadioGroup: ElRadioGroupStub,
          ElSelect: SlotStub,
          ElTable: EmptyStub,
          ElTableColumn: EmptyStub,
          ElTag: SlotStub
        },
        directives: {
          loading: () => undefined
        }
      }
    });
    await flushPromises();

    const workflowStore = useFineJobWorkflowStore();
    workflowStore.page = 3;
    await wrapper.get('[data-value="running"]').trigger("click");
    await flushPromises();

    expect(wrapper.findComponent({ name: "ElTabs" }).exists()).toBe(false);
    expect(workflowStore.selectedStatus).toBe("running");
    expect(workflowStore.page).toBe(1);
    expect(mocks.listFineJobReviewItems).toHaveBeenLastCalledWith(expect.objectContaining({
      status: "approved",
      execution_view: "running",
      page: 1
    }));

    wrapper.unmount();
  });
});
