import { defineStore } from "pinia";
import { ref } from "vue";

export interface NoticeItem {
  id: string;
  kind: "success" | "warning" | "error" | "info";
  title: string;
  message: string;
}

export const useNoticesStore = defineStore("notices", () => {
  const items = ref<NoticeItem[]>([]);

  const push = (notice: Omit<NoticeItem, "id">) => {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    items.value.unshift({ id, ...notice });
    window.setTimeout(() => {
      remove(id);
    }, 6000);
  };

  const remove = (id: string) => {
    items.value = items.value.filter((item) => item.id !== id);
  };

  return {
    items,
    push,
    remove
  };
});
