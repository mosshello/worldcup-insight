<script setup>
import { ref, computed, onMounted } from "vue";
import { RefreshIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
const tab = ref("sources"),
  loading = ref(false),
  error = ref(""),
  data = ref(null),
  evaluating = ref(false);
async function evaluate() {
  evaluating.value = true;
  error.value = "";
  try {
    await api.evaluate();
    await load();
  } catch (e) {
    error.value = e.message;
  } finally {
    evaluating.value = false;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api.capabilities();
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
const sources = computed(() => [
  {
    id: "sporttery",
    name: "体彩官方",
    scope: "比赛 / 五种玩法 SP / 赔率历史",
    ...data.value?.sources.find((s) => s.id === "sporttery"),
  },
  ...(data.value?.sources || []).filter((s) => s.id.startsWith("context:")),
  ...(data.value?.provider_configuration || []),
]);
const statusNames = {
  available: "最近请求成功",
  partial: "部分覆盖",
  error: "最近请求失败",
  not_configured: "未配置",
  pending_validation: "待验收",
};
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>数据与模型</h1>
    </div>
    <t-button variant="outline" :loading="loading" @click="load"
      ><template #icon><RefreshIcon /></template>刷新状态</t-button
    >
  </div>
  <t-alert v-if="error" theme="error" :message="error" class="mb16" />
  <div v-if="data" class="summary-strip">
    <div>
      <span>已采集比赛</span><strong>{{ data.match_count }}</strong>
    </div>
    <div>
      <span>预测版本</span><strong>{{ data.analysis_count }}</strong>
    </div>
    <div>
      <span>已核实赛果</span><strong>{{ data.settled_count }}</strong>
    </div>
  </div>
  <t-tabs v-model="tab" class="detail-tabs"
    ><t-tab-panel value="sources" label="数据来源" /><t-tab-panel
      value="models"
      label="预测模型" /><t-tab-panel
      value="training"
      label="训练与评估" /><t-tab-panel value="competitions" label="赛事维护"
  /></t-tabs>
  <template v-if="tab === 'sources'"
    ><section class="panel">
      <div class="panel-heading">
        <h2>接入状态</h2>
      </div>
      <t-table
        row-key="id"
        :data="sources"
        :loading="loading"
        :columns="[
          { colKey: 'name', title: '数据源', width: 150 },
          { colKey: 'scope', title: '覆盖内容', minWidth: 260 },
          { colKey: 'status', title: '最近状态', width: 160 },
          { colKey: 'checked_at', title: '检查时间', width: 200 },
          { colKey: 'reason', title: '说明', minWidth: 240 },
        ]"
        ><template #status="{ row }"
          ><t-tag
            variant="light"
            :theme="
              row.status === 'available'
                ? 'success'
                : row.status === 'error'
                  ? 'danger'
                  : 'default'
            "
            >{{ statusNames[row.status] || "尚未请求" }}</t-tag
          ></template
        ><template #checked_at="{ row }">{{
          timeLabel(row.checked_at)
        }}</template
        ><template #reason="{ row }">{{
          row.error ||
          row.reason ||
          (row.count != null
            ? `最近读取 ${row.count} 场比赛`
            : "按需读取单场详情")
        }}</template></t-table
      >
    </section>
    <div class="detail-grid">
      <section class="panel">
        <div class="panel-heading"><h2>当前覆盖</h2></div>
        <dl class="context-list">
          <div>
            <dt>后台采集</dt>
            <dd>
              {{ data?.collector?.running ? "运行中 · 每60秒" : "未运行" }}
            </dd>
          </div>
          <div>
            <dt>阵容</dt>
            <dd>未公布时显示待确认</dd>
          </div>
          <div>
            <dt>伤停 / xG</dt>
            <dd>未接入</dd>
          </div>
          <div>
            <dt>AI 解读</dt>
            <dd>
              {{ data?.ai_configured ? "已配置" : "未配置" }}
            </dd>
          </div>
        </dl>
      </section>
    </div></template
  >
  <template v-else-if="tab === 'models'"
    ><section class="panel">
      <div class="panel-heading"><h2>模型版本</h2></div>
      <t-table
        row-key="id"
        :data="data?.models || []"
        :columns="[
          { colKey: 'name', title: '模型', width: 180 },
          { colKey: 'id', title: '版本', minWidth: 220 },
          { colKey: 'method', title: '方法', minWidth: 240 },
          { colKey: 'status', title: '使用状态', width: 120 },
          { colKey: 'validation', title: '验证状态', minWidth: 240 },
        ]"
        empty="模型加载失败"
      /></section
  ></template>
  <template v-else-if="tab === 'competitions'">
    <section class="panel">
      <div class="panel-heading"><h2>赛事覆盖</h2></div>
      <t-table
        row-key="id"
        :data="data?.competitions || []"
        :columns="[
          { colKey: 'name', title: '赛事', width: 100 },
          { colKey: 'id', title: '标识', width: 120 },
          { colKey: 'seasons', title: '赛季', minWidth: 200 },
          { colKey: 'match_count', title: '采集场次', width: 110 },
          { colKey: 'context_count', title: '已获基本面', width: 130 },
          { colKey: 'rule_source', title: '赛制', width: 110 },
        ]"
      >
        <template #seasons="{ row }">{{
          row.seasons.map((s) => s.label).join(" / ")
        }}</template>
        <template #rule_source="{ row }"
          ><a :href="row.rule_source" target="_blank" rel="noopener noreferrer"
            >官方规则 ↗</a
          ></template
        >
      </t-table>
    </section>
  </template>
  <template v-else
    ><section class="panel">
      <div class="panel-heading">
        <h2>离线评估</h2>
        <t-button
          :loading="evaluating"
          :disabled="!data?.training?.ready"
          @click="evaluate"
          >运行评估</t-button
        >
      </div>
      <dl class="context-list">
        <div>
          <dt>历史样本</dt>
          <dd>{{ data?.training?.rows || 0 }} 场</dd>
        </div>
        <div>
          <dt>数据状态</dt>
          <dd>{{ data?.training?.reason || "等待授权历史数据" }}</dd>
        </div>
        <div>
          <dt>评估口径</dt>
          <dd>赛前60分钟 · 时间切分 · 分联赛对比</dd>
        </div>
        <div>
          <dt>生产模型</dt>
          <dd>市场基线</dd>
        </div>
      </dl>
      <t-table
        v-if="data?.training?.evaluation"
        row-key="id"
        :data="[
          {
            id: 'model',
            name: '模型（相同样本）',
            ...data.training.evaluation.paired_model,
          },
          {
            id: 'market',
            name: '市场基线',
            ...data.training.evaluation.market_baseline,
          },
        ]"
        :columns="[
          { colKey: 'name', title: '对照' },
          { colKey: 'count', title: '样本数' },
          { colKey: 'log_loss', title: 'Log loss' },
          { colKey: 'brier', title: 'Brier' },
        ]"
      /></section
  ></template>
</template>
