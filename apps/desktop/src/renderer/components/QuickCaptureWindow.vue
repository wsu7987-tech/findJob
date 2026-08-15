<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Top } from "@element-plus/icons-vue";

import PoolMetadataFields from "./PoolMetadataFields.vue";
import {
  getQuickCaptureWindowState,
  onQuickCaptureScreenshotImage,
  setQuickCaptureAlwaysOnTop,
  startQuickCaptureScreenshot
} from "@/services/desktop-bridge";
import { useQuickCaptureStore } from "@/stores/quickCapture";

const labels = {
  pinned: "\u7f6e\u9876\u5df2\u5f00",
  unpinned: "\u7f6e\u9876\u5df2\u5173",
  titleField: "\u6807\u9898",
  textField: "TXT \u6587\u672c",
  screenshot: "\u622a\u56fe",
  textPlaceholder:
    "\u76f4\u63a5\u7c98\u8d34\u7eaf\u6587\u672c\uff0c\u6216\u70b9\u51fb\u622a\u56fe\u8fdb\u884c OCR \u56de\u586b",
  tagsField: "\u6807\u7b7e",
  categoryField: "\u5206\u7c7b",
  submit: "\u52a0\u5165\u603b\u7ed3\u6c60"
} as const;

const store = useQuickCaptureStore();
const alwaysOnTop = ref(true);
const unsubscribers: Array<() => void> = [];
const hasEnhancedCleaning = computed(() => store.preCleaningBody !== null);

const ocrTagType = computed(() => {
  if (store.loading) {
    return "warning";
  }
  if (store.error) {
    return "danger";
  }
  if (store.lastOcrAt) {
    return "success";
  }
  return "info";
});

const commit = async () => {
  await store.commit();
};

const suggestMetadata = async () => {
  await store.suggestMetadata();
};

const toggleAlwaysOnTop = async () => {
  const nextValue = !alwaysOnTop.value;
  const result = await setQuickCaptureAlwaysOnTop(nextValue);
  alwaysOnTop.value = result?.alwaysOnTop ?? nextValue;
};

const triggerScreenshot = async () => {
  await startQuickCaptureScreenshot();
};

const applyEnhancedCleaning = () => {
  store.applyEnhancedCleaning();
};

const restorePreCleaningBody = () => {
  store.restorePreCleaningBody();
};

const blobToBase64 = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const [, base64 = ""] = result.split(",", 2);
      resolve(base64);
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error("Failed to read pasted image"));
    };
    reader.readAsDataURL(blob);
  });

const handlePaste = async (event: ClipboardEvent) => {
  const items = event.clipboardData?.items;
  if (!items?.length) {
    return;
  }

  if (event.clipboardData?.getData("text/plain")) {
    return;
  }

  const imageItem = Array.from(items).find((item) => item.type.startsWith("image/"));
  if (!imageItem) {
    return;
  }

  const imageFile = imageItem.getAsFile();
  if (!imageFile) {
    return;
  }

  event.preventDefault();
  const imageBase64 = await blobToBase64(imageFile);
  await store.runOcr(imageBase64);
};

onMounted(async () => {
  const state = await getQuickCaptureWindowState();
  alwaysOnTop.value = state?.alwaysOnTop ?? true;
  unsubscribers.push(
    onQuickCaptureScreenshotImage(async (imageBase64) => {
      await store.runOcr(imageBase64);
    })
  );
});

onBeforeUnmount(() => {
  while (unsubscribers.length) {
    const dispose = unsubscribers.pop();
    dispose?.();
  }
});
</script>

<template>
  <section class="quick-capture-window">
    <el-form label-position="top" class="quick-capture-window__form">
      <el-form-item>
        <template #label>
          <div class="quick-capture-window__textarea-label">
            <span>{{ labels.textField }}</span>
            <div class="quick-capture-window__textarea-actions">
              <el-button plain size="small" :loading="store.loading" @click="triggerScreenshot">
                {{ labels.screenshot }}
              </el-button>
              <el-button
                plain
                size="small"
                :type="hasEnhancedCleaning ? 'primary' : undefined"
                @click="hasEnhancedCleaning ? restorePreCleaningBody() : applyEnhancedCleaning()"
              >
                {{ hasEnhancedCleaning ? "还原" : "增强清洗" }}
              </el-button>
              <el-tag size="small" :type="ocrTagType">{{ store.ocrStatusText }}</el-tag>
              <el-tooltip :content="alwaysOnTop ? labels.pinned : labels.unpinned" placement="top">
                <el-button
                  class="quick-capture-window__pin"
                  plain
                  circle
                  size="small"
                  :aria-label="alwaysOnTop ? labels.pinned : labels.unpinned"
                  :type="alwaysOnTop ? 'primary' : undefined"
                  :icon="Top"
                  @click="toggleAlwaysOnTop"
                />
              </el-tooltip>
            </div>
          </div>
        </template>
        <el-input
          v-model="store.body"
          type="textarea"
          :rows="16"
          resize="none"
          name="quick-capture-body"
          autocomplete="off"
          :placeholder="labels.textPlaceholder"
          @paste="handlePaste"
        />
      </el-form-item>

      <el-form-item :label="labels.titleField">
        <el-input v-model="store.title" />
      </el-form-item>

      <PoolMetadataFields
        test-id-prefix="quick-capture"
        :category="store.category"
        :tags="store.tags"
        :suggest-loading="store.suggestingMetadata"
        @update:category="store.category = $event"
        @update:tags="store.tags = $event"
        @suggest="suggestMetadata"
      />
    </el-form>

    <footer class="quick-capture-window__footer">
      <el-button
        class="quick-capture-window__submit"
        type="primary"
        :loading="store.committing"
        @click="commit"
      >
        {{ labels.submit }}
      </el-button>
    </footer>
  </section>
</template>

<style scoped>
.quick-capture-window {
  min-height: 100vh;
  padding: 14px 16px 88px;
  background:
    linear-gradient(180deg, rgba(255, 252, 246, 0.98), rgba(246, 238, 224, 0.98)),
    #f7f0e2;
}

.quick-capture-window__form {
  display: grid;
  gap: 4px;
}

.quick-capture-window__textarea-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.quick-capture-window__textarea-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.quick-capture-window__pin {
  flex-shrink: 0;
}

.quick-capture-window__footer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 12px 16px 16px;
  background: linear-gradient(180deg, rgba(247, 240, 226, 0), rgba(247, 240, 226, 0.96) 32%);
}

.quick-capture-window__submit {
  width: 100%;
}

.quick-capture-window :deep(.el-textarea__inner) {
  min-height: 360px !important;
  font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
  line-height: 1.5;
}

@media (max-width: 640px) {
  .quick-capture-window__textarea-label {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
