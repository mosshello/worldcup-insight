<script setup>
import { ref, reactive, computed, watch } from "vue";
import { MessagePlugin, DialogPlugin } from "tdesign-vue-next";
import { RefreshIcon, AddIcon } from "tdesign-icons-vue-next";
import { request, validTrainingRange } from "./domain";
const props = defineProps({ mode: String, plans: Array });
const emit = defineEmits(["save-plan", "delete-plan"]);
const tab = ref("sources"),
  loading = ref(false),
  error = ref(""),
  report = ref(null),
  formOpen = ref(false),
  formError = ref(""),
  detail = ref(null),
  detailOpen = ref(false),
  compareOpen = ref(false);
const form = reactive({
  name: "俱乐部基线模型",
  start: "2022-07-01",
  end: "2025-06-30",
  testEnd: "2026-06-30",
  algorithm: "poisson",
  leagues: ["英超", "西甲", "意甲", "德甲", "法甲", "欧冠"],
  features: ["球队强度", "主客场", "近期表现"],
});
const sources = computed(() => [
  {
    id: "sporttery",
    name: "体彩场次与 SP",
    scope: "场次 / 开售状态 / 固定奖金",
    provider: "sporttery.cn",
    status: props.mode === "demo" ? "示例快照" : "已接接口",
    note: "场次以官方为准；缓存单独标记。",
  },
  {
    id: "results",
    name: "历史赛果",
    scope: "赛果 / 赛程 / 主客场",
    provider: props.mode === "demo" ? "本地示例" : "现有历史数据集",
    status: props.mode === "demo" ? "示例快照" : "覆盖待核对",
    note: "现有训练数据以国家队为主，俱乐部覆盖需补齐。",
  },
  {
    id: "lineup",
    name: "阵容与伤停",
    scope: "预计首发 / 确认首发 / 伤停",
    provider: "待确定",
    status: "待接入",
    note: "按球员记录来源、发布时间、确认状态，不把缺失视为健康。",
  },
  {
    id: "stats",
    name: "比赛技术统计",
    scope: "xG / 射门 / 压迫 / 机会质量",
    provider: "待确定",
    status: "待接入",
    note: "需采购或授权接入，并统一球队及赛事 ID。",
  },
  {
    id: "odds",
    name: "外盘参考",
    scope: "公司 / 市场 / 赔率时间序列",
    provider: "现有外盘适配器",
    status: props.mode === "demo" ? "示例快照" : "覆盖待核对",
    note: "与体彩玩法分开，不将国外市场当作国内可售玩法。",
  },
]);
const sourceCols = [
  { colKey: "name", title: "数据项", width: 180 },
  { colKey: "scope", title: "覆盖内容", minWidth: 240 },
  { colKey: "provider", title: "来源", minWidth: 170 },
  { colKey: "status", title: "接入状态", width: 150 },
  { colKey: "action", title: "详情", width: 100 },
];
async function load() {
  loading.value = true;
  error.value = "";
  try {
    report.value =
      props.mode === "demo" ? null : await request("/api/console/capabilities");
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
watch(() => props.mode, load, { immediate: true });
function newPlan() {
  formError.value = "";
  formOpen.value = true;
}
function save() {
  if (!form.name.trim()) {
    formError.value = "请填写配置名称";
    return;
  }
  if (!validTrainingRange(form.start, form.end, form.testEnd)) {
    formError.value =
      "日期需满足：训练开始 < 训练结束 < 验证结束。训练与验证严格按时间分隔。";
    return;
  }
  if (!form.leagues.length || !form.features.length) {
    formError.value = "至少选择一项赛事和一项特征";
    return;
  }
  emit("save-plan", {
    ...JSON.parse(JSON.stringify(form)),
    id: crypto.randomUUID(),
    created: new Date().toISOString(),
    status: "待执行",
  });
  formOpen.value = false;
  MessagePlugin.success("训练配置已保存，尚未执行训练");
}
function removePlan(id) {
  const d = DialogPlugin.confirm({
    header: "删除训练配置",
    body: "仅删除这份本地配置，已存在的模型不会受影响。",
    theme: "warning",
    confirmBtn: "删除",
    onConfirm: () => {
      emit("delete-plan", id);
      d.destroy();
    },
    onClose: () => d.destroy(),
  });
}
const planCols = [
  { colKey: "name", title: "配置名称", minWidth: 170 },
  { colKey: "range", title: "训练区间", minWidth: 220 },
  { colKey: "testEnd", title: "验证截止", width: 130 },
  { colKey: "status", title: "状态", width: 100 },
  { colKey: "actions", title: "操作", width: 150 },
];
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">DATA & MODEL</div>
      <h1>数据与模型</h1>
    </div>
    <t-button variant="outline" :loading="loading" @click="load"
      ><template #icon><RefreshIcon /></template>刷新状态</t-button
    >
  </div>
  <t-tabs v-model="tab" class="detail-tabs"
    ><t-tab-panel value="sources" label="数据来源" /><t-tab-panel
      value="training"
      label="训练配置" /><t-tab-panel value="versions" label="模型版本"
  /></t-tabs>
  <t-alert v-if="error" class="mb16" theme="error" :message="error" />
  <template v-if="tab === 'sources'"
    ><section class="panel">
      <div class="panel-heading">
        <h2>数据接入</h2>
        <span class="small muted">接入状态不等于实时可用性</span>
      </div>
      <t-table row-key="id" :data="sources" :columns="sourceCols"
        ><template #status="{ row }"
          ><t-tag
            variant="light"
            :theme="
              row.status === '待接入'
                ? 'default'
                : row.status === '覆盖待核对'
                  ? 'warning'
                  : 'primary'
            "
            >{{ row.status }}</t-tag
          ></template
        ><template #action="{ row }"
          ><t-button
            theme="primary"
            variant="text"
            @click="
              detail = row;
              detailOpen = true;
            "
            >查看详情</t-button
          ></template
        ></t-table
      >
    </section>
    <div class="two-column detail-grid">
      <section class="panel">
        <div class="panel-heading"><h2>数据进入分析前</h2></div>
        <ol class="process-list">
          <li>
            <span>01</span>
            <div>
              <strong>匹配比赛</strong>
              <p>统一球队、赛事、开赛时间与体彩场次 ID。</p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <strong>检查时效与完整性</strong>
              <p>区分缺失、过期、预计及已确认数据。</p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <strong>形成分析快照</strong>
              <p>保留来源、采集时间、模型版本与输入状态。</p>
            </div>
          </li>
        </ol>
      </section>
      <section class="panel">
        <div class="panel-heading"><h2>当前能力边界</h2></div>
        <dl class="context-list">
          <div>
            <dt>国内可售比赛</dt>
            <dd>官方场次列表筛选</dd>
          </div>
          <div>
            <dt>俱乐部阵容 / xG</dt>
            <dd class="warning">待接入</dd>
          </div>
          <div>
            <dt>俱乐部模型训练</dt>
            <dd class="warning">执行服务待接入</dd>
          </div>
          <div>
            <dt>分析与笔记归档</dt>
            <dd>本地浏览器保存</dd>
          </div>
        </dl>
      </section>
    </div></template
  >
  <template v-if="tab === 'training'"
    ><div class="inline-notice">
      第一版可保存训练配置。训练执行服务尚未接入，不生成虚构模型或指标。
    </div>
    <section class="panel">
      <div class="panel-heading">
        <h2>训练配置</h2>
        <t-button @click="newPlan"
          ><template #icon><AddIcon /></template>新建配置</t-button
        >
      </div>
      <t-table
        row-key="id"
        :data="plans"
        :columns="planCols"
        empty="还没有训练配置。点击“新建配置”设置数据范围和特征。"
        ><template #range="{ row }"
          ><span class="mono">{{ row.start }} → {{ row.end }}</span></template
        ><template #actions="{ row }"
          ><t-button
            variant="text"
            theme="primary"
            @click="
              detail = {
                name: row.name,
                scope: row.leagues.join(' / '),
                provider: row.algorithm,
                note: `特征：${row.features.join('、')}。验证区间从 ${row.end} 之后开始，到 ${row.testEnd} 结束。状态：待接入执行服务。`,
              };
              detailOpen = true;
            "
            >详情</t-button
          ><t-button variant="text" theme="danger" @click="removePlan(row.id)"
            >删除</t-button
          ></template
        ></t-table
      >
    </section>
    <section class="panel mt16">
      <div class="panel-heading"><h2>评估要求</h2></div>
      <div class="evaluation-rules">
        <div>
          <strong>按时间切分</strong>
          <p>使用历史训练，向后验证，避免赛后数据进入赛前特征。</p>
        </div>
        <div>
          <strong>与基线比较</strong>
          <p>对照简单球队强度模型与市场基线，检查概率校准。</p>
        </div>
        <div>
          <strong>先影子验证</strong>
          <p>保留旧版，新版独立记录，达标后再决定是否启用。</p>
        </div>
      </div>
    </section></template
  >
  <template v-if="tab === 'versions'"
    ><section class="panel">
      <div class="panel-heading">
        <h2>模型版本</h2>
        <t-button
          variant="outline"
          :disabled="mode !== 'demo'"
          @click="compareOpen = true"
          >查看对比示例</t-button
        >
      </div>
      <template v-if="mode === 'demo'"
        ><t-table
          row-key="id"
          :data="[
            {
              id: 'club-baseline-demo',
              name: '俱乐部基线 · 示例',
              algorithm: 'Elo + Poisson',
              status: '界面示例',
              coverage: '五大联赛 / 欧冠',
            },
            {
              id: 'club-candidate-demo',
              name: '候选模型 · 示例',
              algorithm: 'Elo + Poisson + 近期状态',
              status: '界面示例',
              coverage: '五大联赛 / 欧冠',
            },
          ]"
          :columns="[
            { colKey: 'name', title: '版本' },
            { colKey: 'algorithm', title: '方法' },
            { colKey: 'coverage', title: '覆盖赛事' },
            { colKey: 'status', title: '状态' },
          ]"
        />
        <div class="section-note">
          以下示例不代表已训练或已上线模型。
        </div></template
      >
      <div v-else class="empty-state compact">
        <h3>
          {{
            report?.club_training_enabled
              ? "已连接训练服务"
              : "尚无俱乐部正式模型"
          }}
        </h3>
        <p>现有模型属于国家队分析体系，未作为俱乐部生产模型展示。</p>
        <p v-if="report?.national_model" class="small muted">
          现有模型：{{ report.national_model }}
        </p>
      </div>
    </section></template
  >
  <t-dialog
    v-model:visible="formOpen"
    header="新建训练配置"
    width="640px"
    :close-on-overlay-click="false"
    confirm-btn="保存配置"
    @confirm="save"
    ><div class="training-form">
      <t-alert v-if="formError" theme="error" :message="formError" /><label
        >配置名称 <span class="required">*</span
        ><t-input v-model="form.name" maxlength="50" aria-label="配置名称"
      /></label>
      <div class="two-column">
        <label
          >训练开始<t-date-picker
            v-model="form.start"
            format="YYYY-MM-DD"
            aria-label="训练开始" /></label
        ><label
          >训练结束<t-date-picker
            v-model="form.end"
            format="YYYY-MM-DD"
            aria-label="训练结束"
        /></label>
      </div>
      <label
        >验证结束<t-date-picker
          v-model="form.testEnd"
          format="YYYY-MM-DD"
          aria-label="验证结束" /></label
      ><label
        >模型方法<t-select
          v-model="form.algorithm"
          :options="[{ label: 'Elo + Poisson 基线', value: 'poisson' }]"
          aria-label="模型方法" /></label
      ><label
        >赛事范围<t-checkbox-group
          v-model="form.leagues"
          :options="['英超', '西甲', '意甲', '德甲', '法甲', '欧冠']" /></label
      ><label
        >输入特征<t-checkbox-group
          v-model="form.features"
          :options="['球队强度', '主客场', '近期表现', '休息天数']"
      /></label>
      <p class="muted small">此操作只保存配置，不启动训练、不改变当前模型。</p>
    </div></t-dialog
  >
  <t-drawer
    v-model:visible="detailOpen"
    :header="detail?.name || '详情'"
    size="480px"
    :footer="false"
    ><dl v-if="detail" class="context-list">
      <div>
        <dt>范围</dt>
        <dd>{{ detail.scope }}</dd>
      </div>
      <div>
        <dt>来源 / 方法</dt>
        <dd>{{ detail.provider }}</dd>
      </div>
      <div v-if="detail.status">
        <dt>状态</dt>
        <dd>{{ detail.status }}</dd>
      </div>
    </dl>
    <p class="review-text">{{ detail?.note }}</p></t-drawer
  >
  <t-dialog
    v-model:visible="compareOpen"
    header="模型对比 · 示例"
    width="680px"
    :footer="false"
    ><t-table
      row-key="metric"
      :data="[
        { metric: '验证场次', baseline: '1,200', candidate: '1,200' },
        { metric: 'Log Loss ↓', baseline: '1.042', candidate: '1.058' },
        { metric: 'Brier Score ↓', baseline: '0.612', candidate: '0.625' },
        { metric: '校准误差 ↓', baseline: '0.041', candidate: '0.056' },
      ]"
      :columns="[
        { colKey: 'metric', title: '指标' },
        { colKey: 'baseline', title: '基线模型' },
        { colKey: 'candidate', title: '候选模型' },
      ]" /><t-alert
      theme="warning"
      message="示例结论：候选版未超过基线，不启用。以上指标为设计示意，非真实训练结果。"
      class="mt16"
  /></t-dialog>
</template>
