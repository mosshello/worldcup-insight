<script setup>
import { ref, computed, onMounted } from "vue";
import { RefreshIcon } from "tdesign-icons-vue-next";
import { api, timeLabel } from "./api";
const tab = ref("sources"),
  loading = ref(false),
  error = ref(""),
  data = ref(null);
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
  {
    id: "espn",
    name: "ESPN",
    scope: "近期赛果 / 场地 / 正式首发",
    ...data.value?.sources.find((s) => s.id === "espn"),
  },
]);
const statusNames = {
  available: "最近请求成功",
  partial: "部分覆盖",
  error: "最近请求失败",
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
      label="预测模型" /><t-tab-panel value="training" label="训练与评估"
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
  <template v-else
    ><section class="panel">
      <div class="panel-heading"><h2>训练状态</h2></div>
      <div class="inline-notice">训练服务未接入，基线模型未校准。</div>
    </section></template
  >
</template>
