// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent } from "vue";

import QuickCaptureWindow from "./QuickCaptureWindow.vue";

const GenericStub = defineComponent({
  template: "<div><slot /></div>"
});

const ElFormItemStub = defineComponent({
  props: {
    label: {
      type: String,
      default: ""
    }
  },
  template: "<label><slot name='label'>{{ label }}</slot><slot /></label>"
});

const ElButtonStub = defineComponent({
  emits: ["click"],
  template: "<button type='button' @click=\"$emit('click', $event)\"><slot /></button>"
});

const ElSelectStub = defineComponent({
  props: {
    modelValue: {
      type: [String, Array, null],
      default: null
    }
  },
  template: "<div><slot /></div>"
});

const ElOptionStub = defineComponent({
  template: "<option><slot /></option>"
});

describe("QuickCaptureWindow", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders the quick capture controls", () => {
    const wrapper = mount(QuickCaptureWindow, {
      global: {
        stubs: {
          ElForm: GenericStub,
          ElFormItem: ElFormItemStub,
          ElInput: GenericStub,
          ElSelect: ElSelectStub,
          ElOption: ElOptionStub,
          ElButton: ElButtonStub,
          ElTag: GenericStub,
          ElTooltip: GenericStub,
          ElIcon: GenericStub
        }
      }
    });

    expect(wrapper.text()).toContain("标题");
    expect(wrapper.text()).toContain("TXT 文本");
    expect(wrapper.text()).toContain("截图");
    expect(wrapper.text()).toContain("标签");
    expect(wrapper.text()).toContain("分类");
    expect(wrapper.text()).toContain("建议分类与标签");
    expect(wrapper.text()).toContain("加入总结池");
  });
});
