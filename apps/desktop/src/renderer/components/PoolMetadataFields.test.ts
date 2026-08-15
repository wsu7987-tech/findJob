// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent } from "vue";

import PoolMetadataFields from "./PoolMetadataFields.vue";

const ElFormItemStub = defineComponent({
  props: {
    label: {
      type: String,
      default: ""
    }
  },
  template: "<label><slot name='label'>{{ label }}</slot><slot /></label>"
});

const ElSelectStub = defineComponent({
  inheritAttrs: false,
  props: {
    modelValue: {
      type: [String, Array, null],
      default: null
    },
    multiple: {
      type: Boolean,
      default: false
    },
    placeholder: {
      type: String,
      default: ""
    }
  },
  emits: ["update:modelValue"],
  computed: {
    normalizedValue(): string {
      return Array.isArray(this.modelValue) ? this.modelValue.join(", ") : (this.modelValue ?? "");
    }
  },
  methods: {
    onInput(event: Event) {
      const value = (event.target as HTMLInputElement).value;
      if (this.multiple) {
        this.$emit(
          "update:modelValue",
          value
            .split(/[\n,\uff0c]/)
            .map((item) => item.trim())
            .filter(Boolean)
        );
        return;
      }
      this.$emit("update:modelValue", value || null);
    }
  },
  template:
    "<input v-bind='$attrs' :value='normalizedValue' :placeholder='placeholder' @input='onInput' /><slot />"
});

const ElOptionStub = defineComponent({
  props: {
    label: {
      type: String,
      default: ""
    },
    value: {
      type: [String, Number, null],
      default: null
    }
  },
  template: "<option :value='value ?? \"\"'>{{ label }}</option>"
});

const ElButtonStub = defineComponent({
  emits: ["click"],
  template: "<button type='button' @click=\"$emit('click', $event)\"><slot /></button>"
});

describe("PoolMetadataFields", () => {
  it("emits normalized tags and a suggestion event", async () => {
    const wrapper = mount(PoolMetadataFields, {
      props: {
        category: "",
        tags: []
      },
      global: {
        stubs: {
          ElFormItem: ElFormItemStub,
          ElSelect: ElSelectStub,
          ElOption: ElOptionStub,
          ElButton: ElButtonStub
        }
      }
    });

    await wrapper.get('[data-testid="metadata-category"]').setValue("engineering");
    await wrapper.get('[data-testid="metadata-tags"]').setValue("backend, database , backend");
    await wrapper.get('[data-testid="metadata-suggest"]').trigger("click");

    expect(wrapper.text()).toContain("分类与标签");
    expect(wrapper.text()).toContain("建议分类与标签");
    expect(wrapper.emitted("update:category")).toEqual([["engineering"]]);
    expect(wrapper.emitted("update:tags")).toEqual([[["backend", "database"]]]);
    expect(wrapper.emitted("suggest")).toHaveLength(1);
  });
});
