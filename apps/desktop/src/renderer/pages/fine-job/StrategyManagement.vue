<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import {
  emptyDeliveryStrategy,
  useFineJobDeliveryStrategyStore
} from "@/stores/fineJobDeliveryStrategy";
import { useFineJobResumesStore } from "@/stores/fineJobResumes";
import {
  emptyFilterStrategy,
  emptyRecommendationStrategy,
  useFineJobStrategiesStore
} from "@/stores/fineJobStrategies";
import type {
  FineJobDeliveryStrategy,
  FineJobFilterStrategy,
  FineJobRecommendationStrategy
} from "@/types";

const strategiesStore = useFineJobStrategiesStore();
const deliveryStore = useFineJobDeliveryStrategyStore();
const resumesStore = useFineJobResumesStore();
const activeTab = ref("filters");
const filterForm = ref<FineJobFilterStrategy>(emptyFilterStrategy());
const recommendationForm = ref<FineJobRecommendationStrategy>(emptyRecommendationStrategy());
const deliveryForm = ref<FineJobDeliveryStrategy>(emptyDeliveryStrategy());

const companyScaleOptions = ["0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"];
const companyStageOptions = ["未融资", "不需要融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市"];
const degreeOptions = ["学历不限", "初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士"];
const experienceOptions = ["经验不限", "在校/应届", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上"];
const activeOptions = ["刚刚活跃", "今日活跃", "3日内活跃", "本周活跃", "本月活跃"];
const deliveryReady = computed(() => Boolean(deliveryStore.strategy?.ready));

onMounted(async () => {
  await Promise.all([strategiesStore.load(), resumesStore.load(), deliveryStore.load()]);
  // 策略加载完成后默认选中第一条岗位筛选策略。
  if (strategiesStore.filters.length) editFilter(strategiesStore.filters[0]);
  // 策略加载完成后默认选中第一条岗位建议投递策略。
  if (strategiesStore.recommendations.length) editRecommendation(strategiesStore.recommendations[0]);
  deliveryForm.value = cloneDelivery(deliveryStore.strategy ?? emptyDeliveryStrategy());
});

const editFilter = (strategy?: FineJobFilterStrategy) => {
  filterForm.value = clone(strategy ?? emptyFilterStrategy());
};

const editRecommendation = (strategy?: FineJobRecommendationStrategy) => {
  recommendationForm.value = clone(strategy ?? emptyRecommendationStrategy());
};

const saveFilter = async () => {
  if (!filterForm.value.name.trim()) return ElMessage.warning("请填写策略名称");
  try {
    const saved = await strategiesStore.saveFilter(normalizeFilter(filterForm.value));
    filterForm.value = clone(saved);
    ElMessage.success("岗位筛选策略已保存");
  } catch {
    ElMessage.error(strategiesStore.error ?? "岗位筛选策略保存失败");
  }
};

const saveRecommendation = async () => {
  if (!recommendationForm.value.name.trim()) return ElMessage.warning("请填写策略名称");
  try {
    const saved = await strategiesStore.saveRecommendation(normalizeRecommendation(recommendationForm.value));
    recommendationForm.value = clone(saved);
    ElMessage.success("岗位建议投递策略已保存");
  } catch {
    ElMessage.error(strategiesStore.error ?? "岗位建议投递策略保存失败");
  }
};

const removeFilter = async (strategy: FineJobFilterStrategy) => {
  if (!strategy.id) return;
  await ElMessageBox.confirm(`确认删除“${strategy.name}”？`, "删除筛选策略", { type: "warning" });
  await strategiesStore.removeFilter(strategy.id);
  if (filterForm.value.id === strategy.id) editFilter();
  ElMessage.success("筛选策略已删除");
};

const removeRecommendation = async (strategy: FineJobRecommendationStrategy) => {
  if (!strategy.id) return;
  await ElMessageBox.confirm(`确认删除“${strategy.name}”？`, "删除建议策略", { type: "warning" });
  await strategiesStore.removeRecommendation(strategy.id);
  if (recommendationForm.value.id === strategy.id) editRecommendation();
  ElMessage.success("建议策略已删除");
};

const saveDelivery = async () => {
  try {
    const saved = await deliveryStore.save({
      ...deliveryForm.value,
      daily_greeting_limit: Math.max(1, Math.trunc(deliveryForm.value.daily_greeting_limit)),
      hourly_greeting_limit: Math.max(1, Math.trunc(deliveryForm.value.hourly_greeting_limit)),
      min_match_score: Math.min(1, Math.max(0, Number(deliveryForm.value.min_match_score))),
      notes: deliveryForm.value.notes.trim()
    });
    deliveryForm.value = cloneDelivery(saved ?? deliveryForm.value);
    ElMessage.success("投递执行策略已确认");
  } catch {
    ElMessage.error(deliveryStore.error ?? "投递执行策略保存失败");
  }
};

const normalizeFilter = (value: FineJobFilterStrategy) => ({
  ...value,
  name: value.name.trim(),
  notes: value.notes.trim()
});

const normalizeRecommendation = (value: FineJobRecommendationStrategy) => ({
  ...value,
  name: value.name.trim(),
  work_preferences: value.work_preferences.trim(),
  risk_notes: value.risk_notes.trim(),
  notes: value.notes.trim()
});

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const cloneDelivery = (value: FineJobDeliveryStrategy): FineJobDeliveryStrategy => ({ ...emptyDeliveryStrategy(), ...value });
</script>

<template>
  <section class="page-stack fine-job-page">
    <div class="page-heading">
      <div>
        <p class="panel-eyebrow">Strategy Management</p>
        <h1>策略管理</h1>
        <p class="secondary-text">分别管理岗位硬筛选、投递评估偏好和自动化执行边界。</p>
      </div>
    </div>

    <el-alert v-if="strategiesStore.error" type="error" title="策略管理操作失败" :description="strategiesStore.error" show-icon />

    <section class="page-panel" v-loading="strategiesStore.loading">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="岗位筛选策略" name="filters">
          <div class="strategy-layout filter-strategy-layout">
            <aside class="strategy-list">
              <el-button type="primary" plain @click="editFilter()">新建筛选策略</el-button>
              <span></span>
              <el-button type="primary" :loading="strategiesStore.saving" @click="saveFilter">保存筛选策略</el-button>
              
              <div v-for="strategy in strategiesStore.filters" :key="strategy.id" class="strategy-item" :class="{ active: filterForm.id === strategy.id }" @click="editFilter(strategy)">
                <div><strong>{{ strategy.name }}</strong><el-tag size="small" :type="strategy.enabled ? 'success' : 'info'">{{ strategy.enabled ? "启用" : "停用" }}</el-tag></div>
                <el-button link type="danger" @click.stop="removeFilter(strategy)">删除</el-button>
              </div>
              <el-empty v-if="!strategiesStore.filters.length" description="暂无筛选策略" :image-size="72" />
            </aside>

            <el-form label-position="top" class="strategy-form">
              <div class="form-grid">
                <el-form-item label="策略名称"><el-input v-model="filterForm.name" placeholder="例如：AI Agent 应用正职" /></el-form-item>
                <el-form-item label="启用策略"><el-switch v-model="filterForm.enabled" /></el-form-item>
                <el-form-item label="未知字段处理">
                  <el-select v-model="filterForm.unknown_value_policy">
                    <el-option label="待判断（推荐）" value="review" /><el-option label="保留" value="keep" /><el-option label="排除" value="exclude" />
                  </el-select>
                </el-form-item>
              </div>

              <h3>搜索范围</h3>
              <div class="form-grid">
                <el-form-item label="搜索关键词"><el-select v-model="filterForm.search_keywords" multiple filterable allow-create default-first-option clearable placeholder="AI Agent" /></el-form-item>
                <el-form-item label="城市"><el-select v-model="filterForm.cities" multiple filterable allow-create default-first-option clearable placeholder="广州" /></el-form-item>
                <el-form-item label="工作性质">
                  <el-select v-model="filterForm.job_types" multiple clearable>
                    <el-option label="正职" value="full_time" /><el-option label="实习" value="internship" /><el-option label="兼职" value="part_time" />
                  </el-select>
                </el-form-item>
              </div>

              <h3>岗位与公司</h3>
              <div class="form-grid">
                <el-form-item label="岗位名称包含任一"><el-select v-model="filterForm.title_include_any" multiple filterable allow-create default-first-option clearable placeholder="Agent、智能体" /></el-form-item>
                <el-form-item label="岗位名称必须全部包含"><el-select v-model="filterForm.title_include_all" multiple filterable allow-create default-first-option clearable placeholder="应用、开发" /></el-form-item>
                <el-form-item label="岗位名称排除"><el-select v-model="filterForm.title_exclude" multiple filterable allow-create default-first-option clearable placeholder="销售、讲师" /></el-form-item>
                <el-form-item label="限定公司"><el-select v-model="filterForm.company_include" multiple filterable allow-create default-first-option clearable placeholder="公司名称" /></el-form-item>
                <el-form-item label="排除公司"><el-select v-model="filterForm.company_exclude" multiple filterable allow-create default-first-option clearable placeholder="外包公司" /></el-form-item>
                <el-form-item label="公司规模">
                  <el-select v-model="filterForm.company_scales" multiple clearable><el-option v-for="item in companyScaleOptions" :key="item" :label="item" :value="item" /></el-select>
                </el-form-item>
                <el-form-item label="公司行业"><el-select v-model="filterForm.company_industries" multiple filterable allow-create default-first-option clearable placeholder="人工智能、互联网" /></el-form-item>
                <el-form-item label="融资阶段">
                  <el-select v-model="filterForm.company_stages" multiple clearable><el-option v-for="item in companyStageOptions" :key="item" :label="item" :value="item" /></el-select>
                </el-form-item>
              </div>

              <h3>任职条件与薪资</h3>
              <div class="form-grid">
                <el-form-item label="学历要求">
                  <el-select v-model="filterForm.degrees" multiple clearable><el-option v-for="item in degreeOptions" :key="item" :label="item" :value="item" /></el-select>
                </el-form-item>
                <el-form-item label="经验要求">
                  <el-select v-model="filterForm.experiences" multiple clearable><el-option v-for="item in experienceOptions" :key="item" :label="item" :value="item" /></el-select>
                </el-form-item>
                <el-form-item label="招聘者活跃状态">
                  <el-select v-model="filterForm.boss_active_statuses" multiple clearable><el-option v-for="item in activeOptions" :key="item" :label="item" :value="item" /></el-select>
                </el-form-item>
                <el-form-item label="月薪下限不低于（K）"><el-input-number v-model="filterForm.monthly_salary_min" :min="0" /></el-form-item>
                <el-form-item label="月薪上限不低于（K）"><el-input-number v-model="filterForm.monthly_salary_max_at_least" :min="0" /></el-form-item>
                <el-form-item label="日薪下限不低于（元）"><el-input-number v-model="filterForm.daily_salary_min" :min="0" /></el-form-item>
              </div>

              <h3>技能与内容</h3>
              <div class="form-grid">
                <el-form-item label="技能包含任一"><el-select v-model="filterForm.skill_include_any" multiple filterable allow-create default-first-option clearable placeholder="LangGraph、RAG" /></el-form-item>
                <el-form-item label="技能必须全部包含"><el-select v-model="filterForm.skill_include_all" multiple filterable allow-create default-first-option clearable placeholder="Python" /></el-form-item>
                <el-form-item label="技能/JD 排除"><el-select v-model="filterForm.skill_exclude" multiple filterable allow-create default-first-option clearable placeholder="驻场、电话销售" /></el-form-item>
              </div>
              <el-form-item label="备注"><el-input v-model="filterForm.notes" type="textarea" :rows="3" /></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="岗位建议投递策略" name="recommendations">
          <div class="strategy-layout recommendation-strategy-layout">
            <aside class="strategy-list">
              <el-button type="primary" plain @click="editRecommendation()">新建建议策略</el-button>
              <span></span>
              <el-button type="primary" :loading="strategiesStore.saving" @click="saveRecommendation">保存建议策略</el-button>
              <div v-for="strategy in strategiesStore.recommendations" :key="strategy.id" class="strategy-item" :class="{ active: recommendationForm.id === strategy.id }" @click="editRecommendation(strategy)">
                <div><strong>{{ strategy.name }}</strong><el-tag size="small" :type="strategy.enabled ? 'success' : 'info'">{{ strategy.evaluation_method }}</el-tag></div>
                <el-button link type="danger" @click.stop="removeRecommendation(strategy)">删除</el-button>
              </div>
              <el-empty v-if="!strategiesStore.recommendations.length" description="暂无建议策略" :image-size="72" />
            </aside>

            <el-form label-position="top" class="strategy-form">
              <div class="form-grid">
                <el-form-item label="策略名称"><el-input v-model="recommendationForm.name" placeholder="例如：AI Agent 应用岗位评估" /></el-form-item>
                <el-form-item label="评估方式">
                  <el-select v-model="recommendationForm.evaluation_method"><el-option label="规则 + LLM（推荐）" value="hybrid" /><el-option label="仅规则，零 Token" value="rules" /><el-option label="仅 LLM" value="llm" /></el-select>
                </el-form-item>
                <el-form-item label="启用策略"><el-switch v-model="recommendationForm.enabled" /></el-form-item>
                <el-form-item label="关联筛选策略">
                  <el-select v-model="recommendationForm.filter_strategy_id" clearable><el-option v-for="item in strategiesStore.filters" :key="item.id" :label="item.name" :value="item.id" /></el-select>
                </el-form-item>
                <el-form-item label="关联简历">
                  <el-select v-model="recommendationForm.resume_id" clearable><el-option v-for="item in resumesStore.resumes" :key="item.id" :label="item.name" :value="item.id" /></el-select>
                </el-form-item>
                <el-form-item label="最低推荐置信度"><el-slider v-model="recommendationForm.minimum_confidence" :min="0" :max="1" :step="0.05" show-input /></el-form-item>
              </div>
              <div class="form-grid">
                <el-form-item label="期望职责"><el-select v-model="recommendationForm.desired_responsibilities" multiple filterable allow-create default-first-option clearable placeholder="Agent 应用落地" /></el-form-item>
                <el-form-item label="必备技能"><el-select v-model="recommendationForm.required_skills" multiple filterable allow-create default-first-option clearable placeholder="Python" /></el-form-item>
                <el-form-item label="加分技能"><el-select v-model="recommendationForm.preferred_skills" multiple filterable allow-create default-first-option clearable placeholder="LangGraph、MCP" /></el-form-item>
                <el-form-item label="排除职责/风险词"><el-select v-model="recommendationForm.excluded_terms" multiple filterable allow-create default-first-option clearable placeholder="销售、驻场" /></el-form-item>
                <el-form-item label="偏好行业"><el-select v-model="recommendationForm.preferred_industries" multiple filterable allow-create default-first-option clearable placeholder="人工智能" /></el-form-item>
                <el-form-item label="信息不足时"><el-select v-model="recommendationForm.insufficient_info_action"><el-option label="待人工判断" value="review" /><el-option label="不建议" value="reject" /></el-select></el-form-item>
              </div>
              <el-form-item label="工作偏好"><el-input v-model="recommendationForm.work_preferences" type="textarea" :rows="2" placeholder="远程、通勤、团队阶段等软要求" /></el-form-item>
              <el-form-item label="风险偏好"><el-input v-model="recommendationForm.risk_notes" type="textarea" :rows="2" /></el-form-item>
              <el-form-item label="其他说明"><el-input v-model="recommendationForm.notes" type="textarea" :rows="2" /></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane label="投递执行策略" name="delivery">
          <div class="panel-title-row"><div><h2>自动化与风控边界</h2><p class="secondary-text">不参与岗位筛选，只约束后续投递动作。</p></div><el-tag :type="deliveryReady ? 'success' : 'warning'">{{ deliveryReady ? "已确认" : "未确认" }}</el-tag></div>
          <el-form label-position="top">
            <div class="form-grid">
              <el-form-item label="自动化等级"><el-select v-model="deliveryForm.automation_level"><el-option label="辅助模式" value="assist" /><el-option label="半自动" value="semi_auto" /><el-option label="自动打招呼" value="auto_greeting" /></el-select></el-form-item>
              <el-form-item label="允许自动打招呼"><el-switch v-model="deliveryForm.auto_greeting_enabled" /></el-form-item>
              <el-form-item label="发送后强制刷新验证沟通状态">
                <el-switch v-model="deliveryForm.force_contact_verification_enabled" />
                <p class="secondary-text verification-help">
                  开启后，平台返回成功时会随机等待10～30秒，刷新当前岗位页面一次并检查是否已变为“继续沟通”。
                  每个岗位会额外增加10～30秒等待及页面加载时间；页面异常时最长还可能增加一次30秒状态等待。
                </p>
              </el-form-item>
              <el-form-item label="遇到风险立即暂停"><el-switch v-model="deliveryForm.pause_on_risk" /></el-form-item>
              <el-form-item label="每日打招呼上限"><el-input-number v-model="deliveryForm.daily_greeting_limit" :min="1" :max="500" /></el-form-item>
              <el-form-item label="每小时打招呼上限"><el-input-number v-model="deliveryForm.hourly_greeting_limit" :min="1" :max="100" /></el-form-item>
              <el-form-item label="最低匹配分"><el-slider v-model="deliveryForm.min_match_score" :min="0" :max="1" :step="0.01" show-input /></el-form-item>
              <el-form-item label="投递简历">
                <el-select v-model="deliveryForm.resume_submit_mode"><el-option label="必须人工确认" value="manual" /><el-option label="收到 HR 邀请且匹配时自动投递" value="auto_on_invite" /></el-select>
              </el-form-item>
              <el-form-item label="交换联系方式">
                <el-select v-model="deliveryForm.contact_share_mode"><el-option label="必须人工确认" value="manual" /><el-option label="匹配通过后自动发送" value="auto_after_match" /></el-select>
              </el-form-item>
              <el-form-item label="面试时间">
                <el-select v-model="deliveryForm.interview_accept_mode"><el-option label="必须人工确认" value="manual" /><el-option label="命中可选时间段时自动接受" value="auto_in_selected_slots" /></el-select>
              </el-form-item>
              <el-form-item label="只接受线上面试"><el-switch v-model="deliveryForm.only_online_interview" /></el-form-item>
            </div>
            <el-form-item label="备注"><el-input v-model="deliveryForm.notes" type="textarea" :rows="3" /></el-form-item>
            <el-button type="primary" :loading="deliveryStore.saving" @click="saveDelivery">确认执行策略</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </section>
  </section>
</template>

<style scoped>
.strategy-layout { display: grid; grid-template-columns: minmax(220px, 280px) 1fr; gap: 24px; }
.filter-strategy-layout,
.recommendation-strategy-layout {
  height: min(1000px, calc(100vh - 260px));
  min-height: 0;
  align-items: start;
  grid-template-rows: minmax(0, 1fr);
}
.filter-strategy-layout > .strategy-list,
.filter-strategy-layout > .strategy-form,
.recommendation-strategy-layout > .strategy-list,
.recommendation-strategy-layout > .strategy-form {
  height: 100%;
  max-height: 1000px;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--el-border-color) transparent;
}
.filter-strategy-layout > .strategy-list::-webkit-scrollbar,
.filter-strategy-layout > .strategy-form::-webkit-scrollbar,
.recommendation-strategy-layout > .strategy-list::-webkit-scrollbar,
.recommendation-strategy-layout > .strategy-form::-webkit-scrollbar { width: 4px; }
.filter-strategy-layout > .strategy-list::-webkit-scrollbar-thumb,
.filter-strategy-layout > .strategy-form::-webkit-scrollbar-thumb,
.recommendation-strategy-layout > .strategy-list::-webkit-scrollbar-thumb,
.recommendation-strategy-layout > .strategy-form::-webkit-scrollbar-thumb {
  background: var(--el-border-color);
  border-radius: 999px;
}
.strategy-list { display: grid; align-content: start; gap: 10px; }
.strategy-item { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px; border: 1px solid var(--el-border-color); border-radius: 10px; cursor: pointer; }
.strategy-item.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.strategy-item > div { display: flex; align-items: center; gap: 8px; min-width: 0; }
.strategy-form { min-width: 0; }
.strategy-form h3 { margin: 18px 0 10px; }
.verification-help { margin: 8px 0 0; line-height: 1.55; }
@media (max-width: 900px) { .strategy-layout { grid-template-columns: 1fr; } }
</style>
