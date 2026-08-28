<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { chooseFile, hasFilePicker } from "@/services/desktop-bridge";
import { formatDateTime } from "@/services/format";
import { useFineJobResumesStore } from "@/stores/fineJobResumes";
import type { FineJobResume, FineJobResumeFact } from "@/types";

const resumesStore = useFineJobResumesStore();
const supportsFilePicker = hasFilePicker();
const parseDialogOpen = ref(false);
const confirmDialogOpen = ref(false);
const factDrafts = ref<FineJobResumeFact[]>([]);

const selectedResume = computed(() => resumesStore.selectedResume);

onMounted(() => {
  void resumesStore.load();
});

const uploadResume = async () => {
  const filePath = await chooseFile({
    title: "选择简历 PDF",
    filters: [{ name: "PDF 简历", extensions: ["pdf"] }]
  });
  if (!filePath) {
    return;
  }

  try {
    await resumesStore.createFromFile(filePath, "auto");
    ElMessage.success("简历解析完成");
  } catch {
    ElMessage.error(resumesStore.error ?? "简历解析失败");
  }
};

const removeResume = async (resume: FineJobResume) => {
  try {
    await ElMessageBox.confirm(
      `确定删除简历“${resume.name}”吗？已确认的信息也会一并删除。`,
      "删除简历",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消"
      }
    );
    await resumesStore.deleteResume(resume.id);
    parseDialogOpen.value = false;
    confirmDialogOpen.value = false;
    ElMessage.success("简历已删除");
  } catch (value) {
    if (value === "cancel" || value === "close") {
      return;
    }
    ElMessage.error(resumesStore.error ?? "简历删除失败");
  }
};

const openParseDialog = async (resume: FineJobResume) => {
  await resumesStore.selectResume(resume.id);
  parseDialogOpen.value = true;
};

const openConfirmDialog = async (resume: FineJobResume) => {
  await resumesStore.selectResume(resume.id);
  let facts = await resumesStore.loadFacts(resume.id);
  if (facts.length === 0) {
    facts = await resumesStore.extractFacts(resume.id);
  }
  factDrafts.value = facts.map((fact) => ({ ...fact }));
  confirmDialogOpen.value = true;
};

const openConfirmFromParse = async () => {
  if (!selectedResume.value) {
    return;
  }
  parseDialogOpen.value = false;
  await openConfirmDialog(selectedResume.value);
};

const factStatusLabel = (resume: FineJobResume) => {
  const facts = resumesStore.facts[resume.id] ?? [];
  if (facts.length === 0) {
    return "待确认";
  }
  return facts.every((fact) => fact.user_confirmed) ? "已确认" : "待确认";
};

const factStatusType = (resume: FineJobResume) =>
  factStatusLabel(resume) === "已确认" ? "success" : "warning";

const addFact = () => {
  if (!selectedResume.value) {
    return;
  }
  factDrafts.value.push({
    id: null,
    resume_id: selectedResume.value.id,
    fact_type: "basic",
    fact_key: "",
    fact_value: "",
    confidence: 1,
    source_text: null,
    user_confirmed: true,
    sensitive: false
  });
};

const removeFact = (index: number) => {
  factDrafts.value.splice(index, 1);
};

const saveFacts = async () => {
  if (!selectedResume.value) {
    return;
  }
  const cleanedFacts = factDrafts.value.filter(
    (fact) => fact.fact_key.trim() && fact.fact_value.trim()
  );
  try {
    factDrafts.value = await resumesStore.saveFacts(selectedResume.value.id, cleanedFacts);
    ElMessage.success("确认信息已保存");
    confirmDialogOpen.value = false;
  } catch {
    ElMessage.error(resumesStore.error ?? "确认信息保存失败");
  }
};

const factTypeOptions = [
  { label: "基础信息", value: "basic" },
  { label: "联系方式", value: "contact" },
  { label: "教育经历", value: "education" },
  { label: "工作经历", value: "experience" },
  { label: "项目经历", value: "project" },
  { label: "技能", value: "skill" }
];
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Resume Profile</p>
        <h1>简历资料</h1>
        <p class="secondary-text">上传并解析简历。解析全文和信息确认都通过弹框处理，主页面只保留状态列表。</p>
      </div>
      <div class="card-actions">
        <el-button :loading="resumesStore.loading" @click="resumesStore.load()">刷新</el-button>
        <el-button
          type="primary"
          :disabled="!supportsFilePicker"
          :loading="resumesStore.parsing"
          @click="uploadResume"
        >
          上传简历
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="!supportsFilePicker"
      type="warning"
      title="当前环境不支持桌面文件选择"
      description="请在 Electron 桌面端使用简历上传功能。"
      show-icon
    />

    <el-alert
      v-if="resumesStore.error"
      type="error"
      title="简历操作失败"
      :description="resumesStore.error"
      show-icon
    />

    <section class="table-panel resume-table-panel">
      <el-table
        v-loading="resumesStore.loading || resumesStore.parsing"
        :data="resumesStore.resumes"
        empty-text="还没有简历，请先上传"
      >
        <el-table-column prop="name" label="简历名称" min-width="220" />
        <el-table-column label="解析状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.status === 'parsed' ? 'success' : 'danger'">
              {{ row.status === "parsed" ? "已解析" : "失败" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="信息确认" width="130">
          <template #default="{ row }">
            <el-tag :type="factStatusType(row)">{{ factStatusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="190">
          <template #default="{ row }">
            {{ formatDateTime(row.updated_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <div class="table-actions">
              <el-button size="small" plain @click="openParseDialog(row)">查看解析</el-button>
              <el-button size="small" type="primary" plain @click="openConfirmDialog(row)">
                信息确认
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :loading="resumesStore.deleting"
                @click="removeResume(row)"
              >删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-drawer
      v-model="parseDialogOpen"
      class="resume-side-drawer"
      size="640px"
      title="解析结果"
      direction="rtl"
    >
      <div v-if="selectedResume" class="resume-dialog-body">
        <div class="meta-list">
          <div>
            <span>解析方式</span>
            <strong>{{ selectedResume.parser_name }}</strong>
          </div>
          <div>
            <span>页数</span>
            <strong>{{ selectedResume.page_count }}</strong>
          </div>
          <div>
            <span>字符数</span>
            <strong>{{ selectedResume.char_count }}</strong>
          </div>
          <div>
            <span>OCR</span>
            <strong>{{ selectedResume.is_ocr ? "是" : "否" }}</strong>
          </div>
          <div>
            <span>质量评分</span>
            <strong>{{ selectedResume.quality_score.toFixed(2) }}</strong>
          </div>
        </div>

        <el-alert
          v-if="selectedResume.warnings.length"
          type="warning"
          title="解析提示"
          :description="selectedResume.warnings.join('；')"
          show-icon
        />

        <pre class="resume-preview__text">{{ selectedResume.raw_text || selectedResume.preview_text }}</pre>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <el-button @click="parseDialogOpen = false">关闭</el-button>
        <el-button type="primary" @click="openConfirmFromParse">进入信息确认</el-button>
        </div>
      </template>
    </el-drawer>

    <el-drawer
      v-model="confirmDialogOpen"
      class="resume-side-drawer"
      size="560px"
      title="信息确认"
      direction="rtl"
    >
      <div v-if="selectedResume" v-loading="resumesStore.factsLoading" class="resume-confirm-body">
        <el-alert
          type="info"
          title="简历资料不是开始投递的必需项"
          description="这里确认的信息主要用于 JD 匹配、简历投递判断和后续回复草稿增强。"
          show-icon
        />

        <div class="fact-list">
          <article v-for="(fact, index) in factDrafts" :key="fact.id ?? index" class="fact-card">
            <div class="fact-card__header">
              <el-select v-model="fact.fact_type" size="small" class="fact-card__type">
                <el-option
                  v-for="option in factTypeOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
              <el-switch
                v-model="fact.sensitive"
                size="small"
                active-text="敏感"
                inactive-text="普通"
              />
              <el-button size="small" type="danger" plain @click="removeFact(index)">删除</el-button>
            </div>

            <el-input v-model="fact.fact_key" placeholder="字段名，例如 手机号 / 教育经历" />
            <el-input
              v-model="fact.fact_value"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 6 }"
              placeholder="确认后的信息"
            />
            <p v-if="fact.source_text" class="fact-card__source">
              来源：{{ fact.source_text }}
            </p>
          </article>
        </div>

        <el-empty v-if="factDrafts.length === 0" description="还没有可确认的信息">
          <el-button type="primary" @click="addFact">手动添加</el-button>
        </el-empty>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <el-button @click="addFact">添加信息</el-button>
          <el-button @click="confirmDialogOpen = false">关闭</el-button>
          <el-button type="primary" :loading="resumesStore.factsSaving" @click="saveFacts">
            保存确认
          </el-button>
        </div>
      </template>
    </el-drawer>
  </section>
</template>
