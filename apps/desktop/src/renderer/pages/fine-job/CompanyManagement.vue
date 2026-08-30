<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { api } from "@/services/api";
import { formatDateTime } from "@/services/format";
import type { FineJobCompany, FineJobCompanyType } from "@/types";

const loading = ref(false);
const saving = ref(false);
const items = ref<FineJobCompany[]>([]);
const total = ref(0);
const filters = reactive({
  query: "",
  company_type: "" as FineJobCompanyType | "",
  blacklist_status: "all" as "all" | "blacklisted" | "normal",
  page: 1,
  page_size: 20
});
const editorOpen = ref(false);
const editingId = ref<string | null>(null);
const batchEditorOpen = ref(false);
const batchSaving = ref(false);
const batchCompanyNames = ref("");
const editor = reactive({
  canonical_name: "",
  company_type: "unknown" as FineJobCompanyType,
  notes: "",
  alias_name: ""
});

const outsourcingExample = "中软，软通，博彦科技，东软，文思海辉，上海思芮，佰钧成，微创，埃森哲，神州数码，德科，法本，京北方，中科软，润和，信必优，纬创，神州信息，新致软件，中电金信，亿达信息，柯莱特，博朗软件，亚信科技，宇信科技，高伟达，凯捷";

const companyTypeLabel = (value: FineJobCompanyType) => ({
  unknown: "待确认",
  direct: "直招公司",
  outsourcing: "外包公司"
}[value]);

const load = async () => {
  loading.value = true;
  try {
    const response = await api.listFineJobCompanies(filters);
    items.value = response.items;
    total.value = response.total;
  } catch (error) {
    ElMessage.error((error as Error).message || "公司列表加载失败");
  } finally {
    loading.value = false;
  }
};

const search = () => {
  filters.page = 1;
  void load();
};

const openEditor = (company?: FineJobCompany) => {
  editingId.value = company?.id ?? null;
  editor.canonical_name = company?.canonical_name ?? "";
  editor.company_type = company?.company_type ?? "unknown";
  editor.notes = company?.notes ?? "";
  editor.alias_name = "";
  editorOpen.value = true;
};

const openBatchEditor = () => {
  batchCompanyNames.value = "";
  batchEditorOpen.value = true;
};

const fillOutsourcingExample = () => {
  batchCompanyNames.value = outsourcingExample;
};

const importOutsourcingCompanies = async () => {
  // 兼容常见粘贴格式，并在提交前合并重复的公司名称。
  const companyNames = [...new Set(
    batchCompanyNames.value
      .split(/[，,\n]/)
      .map((name) => name.trim())
      .filter(Boolean)
  )];
  if (!companyNames.length) return ElMessage.warning("请输入至少一家外包公司");

  batchSaving.value = true;
  const failedNames: string[] = [];
  try {
    for (const name of companyNames) {
      try {
        await api.createFineJobCompany({
          name,
          company_type: "outsourcing"
        });
      } catch {
        failedNames.push(name);
      }
    }
    await load();
    const importedCount = companyNames.length - failedNames.length;
    if (!failedNames.length) {
      batchEditorOpen.value = false;
      ElMessage.success(`已录入 ${importedCount} 家外包公司`);
      return;
    }
    ElMessage.warning(`已录入 ${importedCount} 家；未录入：${failedNames.join("、")}`);
  } finally {
    batchSaving.value = false;
  }
};

const save = async () => {
  if (!editor.canonical_name.trim()) return ElMessage.warning("请填写公司名称");
  saving.value = true;
  try {
    if (editingId.value) {
      await api.updateFineJobCompany(editingId.value, {
        canonical_name: editor.canonical_name.trim(),
        company_type: editor.company_type,
        notes: editor.notes.trim()
      });
    } else {
      await api.createFineJobCompany({
        name: editor.canonical_name.trim(),
        company_type: editor.company_type,
        notes: editor.notes.trim()
      });
    }
    editorOpen.value = false;
    await load();
    ElMessage.success("公司信息已保存");
  } catch (error) {
    ElMessage.error((error as Error).message || "公司信息保存失败");
  } finally {
    saving.value = false;
  }
};

const addAlias = async (company: FineJobCompany) => {
  try {
    const { value } = await ElMessageBox.prompt("输入该公司的另一个名称", "添加公司别名", {
      inputPlaceholder: "公司简称、历史名称或平台展示名",
      inputPattern: /\S+/,
      inputErrorMessage: "别名不能为空"
    });
    await api.addFineJobCompanyAlias(company.id, value.trim());
    await load();
    ElMessage.success("公司别名已添加");
  } catch (action) {
    if (action !== "cancel" && action !== "close") {
      ElMessage.error((action as Error).message || "公司别名添加失败");
    }
  }
};

const removeAlias = async (company: FineJobCompany, aliasId: string) => {
  await api.deleteFineJobCompanyAlias(company.id, aliasId);
  await load();
  ElMessage.success("公司别名已移除");
};

const toggleBlacklist = async (company: FineJobCompany) => {
  let reason = "";
  if (!company.is_blacklisted) {
    try {
      const result = await ElMessageBox.prompt("请填写加入黑名单的原因", "加入公司黑名单", {
        inputPlaceholder: "例如：虚假招聘、岗位质量低",
        inputPattern: /\S+/,
        inputErrorMessage: "请填写黑名单原因"
      });
      reason = result.value.trim();
    } catch {
      return;
    }
  }
  await api.setFineJobCompanyBlacklist(company.id, !company.is_blacklisted, reason);
  await load();
  ElMessage.success(company.is_blacklisted ? "已移出公司黑名单" : "已加入公司黑名单");
};

onMounted(load);
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Company Governance</p>
        <h1>公司管理</h1>
        <p class="secondary-text">统一维护外包公司、直招公司、公司别名和全局黑名单；岗位标签与筛选门禁同步生效。</p>
      </div>
      <div class="page-actions">
        <el-button @click="openBatchEditor">批量录入外包公司</el-button>
        <el-button type="primary" @click="openEditor()">新增公司</el-button>
      </div>
    </div>

    <section class="page-panel company-filters">
      <el-input v-model="filters.query" clearable placeholder="搜索公司名称或别名" @keyup.enter="search" />
      <el-select v-model="filters.company_type" clearable placeholder="全部公司类型" @change="search">
        <el-option label="待确认" value="unknown" />
        <el-option label="直招公司" value="direct" />
        <el-option label="外包公司" value="outsourcing" />
      </el-select>
      <el-select v-model="filters.blacklist_status" @change="search">
        <el-option label="全部黑名单状态" value="all" />
        <el-option label="仅黑名单" value="blacklisted" />
        <el-option label="仅正常公司" value="normal" />
      </el-select>
      <el-button :loading="loading" @click="search">查询</el-button>
    </section>

    <section class="table-panel">
      <el-table v-loading="loading" :data="items" empty-text="暂无公司记录">
        <el-table-column label="公司" min-width="220">
          <template #default="{ row }">
            <div class="company-name">
              <strong>{{ row.canonical_name }}</strong>
              <div>
                <el-tag :type="row.company_type === 'outsourcing' ? 'warning' : row.company_type === 'direct' ? 'success' : 'info'" size="small">
                  {{ companyTypeLabel(row.company_type) }}
                </el-tag>
                <el-tag v-if="row.is_blacklisted" type="danger" size="small">黑名单</el-tag>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="别名" min-width="200">
          <template #default="{ row }">
            <div class="alias-list">
              <el-tag v-for="alias in row.aliases" :key="alias.id" closable size="small" @close="removeAlias(row, alias.id)">
                {{ alias.alias_name }}
              </el-tag>
              <el-button link type="primary" @click="addAlias(row)">添加别名</el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="岗位事实" min-width="185">
          <template #default="{ row }">
            <span>岗位 {{ row.job_count }} · 已投递 {{ row.applied_job_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近事件" min-width="230">
          <template #default="{ row }">
            <div class="event-list">
              <span>详情：{{ row.last_detail_at ? formatDateTime(row.last_detail_at) : "无" }}</span>
              <span>建议：{{ row.last_evaluated_at ? formatDateTime(row.last_evaluated_at) : "无" }}</span>
              <span>投递：{{ row.last_applied_at ? formatDateTime(row.last_applied_at) : "无" }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="notes" label="备注 / 黑名单原因" min-width="200">
          <template #default="{ row }">{{ row.is_blacklisted ? row.blacklist_reason : row.notes || "—" }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEditor(row)">编辑</el-button>
            <el-button link :type="row.is_blacklisted ? 'success' : 'danger'" @click="toggleBlacklist(row)">
              {{ row.is_blacklisted ? "移出黑名单" : "加入黑名单" }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="filters.page"
        v-model:page-size="filters.page_size"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @change="load"
      />
    </section>

    <el-dialog v-model="editorOpen" :title="editingId ? '编辑公司' : '新增公司'" width="560px">
      <el-form label-position="top">
        <el-form-item label="公司标准名"><el-input v-model="editor.canonical_name" /></el-form-item>
        <el-form-item label="公司类型">
          <el-radio-group v-model="editor.company_type">
            <el-radio-button value="unknown">待确认</el-radio-button>
            <el-radio-button value="direct">直招公司</el-radio-button>
            <el-radio-button value="outsourcing">外包公司</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="editor.notes" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="batchEditorOpen" title="批量录入外包公司" width="560px">
      <el-form label-position="top">
        <el-form-item label="外包公司">
          <el-input
            v-model="batchCompanyNames"
            data-testid="batch-outsourcing-input"
            type="textarea"
            :rows="7"
            placeholder="例如：中软，软通，博彦科技"
          />
          <p class="form-help">请以逗号分隔，支持中文逗号、英文逗号和换行。</p>
        </el-form-item>
        <el-button data-testid="fill-outsourcing-example" @click="fillOutsourcingExample">获取示例外包</el-button>
      </el-form>
      <template #footer>
        <el-button :disabled="batchSaving" @click="batchEditorOpen = false">取消</el-button>
        <el-button data-testid="batch-outsourcing-submit" type="primary" :loading="batchSaving" @click="importOutsourcingCompanies">批量录入</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.page-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.company-filters { display: grid; grid-template-columns: minmax(240px, 1fr) 180px 180px auto; gap: 12px; }
.company-name, .event-list { display: grid; gap: 6px; }
.company-name > div, .alias-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.event-list { font-size: 12px; color: var(--el-text-color-secondary); }
.form-help { margin: 8px 0 0; color: var(--el-text-color-secondary); font-size: 12px; }
.table-panel .el-pagination { margin-top: 16px; justify-content: flex-end; }
@media (max-width: 900px) { .company-filters { grid-template-columns: 1fr; } }
</style>
