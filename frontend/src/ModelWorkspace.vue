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
      <div class="eyebrow">DATA & MODEL</div>
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
    <div>
      <span>记录存储</span>
      <p>
        本机 SQLite 数据库<br /><span class="muted small"
          >结构化快照与版本持久保存</span
        >
      </p>
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
        <span class="small muted">按最近一次请求记录，非可用性保证</span>
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
    <div class="two-column detail-grid">
      <section class="panel">
        <div class="panel-heading"><h2>数据进入分析前</h2></div>
        <ol class="process-list">
          <li>
            <span>01</span>
            <div>
              <strong>核对比赛身份</strong>
              <p>赛事、双方球队和开赛时间均一致才关联。</p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <strong>检查来源时间</strong>
              <p>采集时间与来源更新时间分别保存，过期数据标记。</p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <strong>保存输入快照</strong>
              <p>每版预测绑定输入与计算版本，赛后数据不能改写赛前判断。</p>
            </div>
          </li>
        </ol>
      </section>
      <section class="panel">
        <div class="panel-heading"><h2>当前覆盖</h2></div>
        <dl class="context-list">
          <div>
            <dt>阵容</dt>
            <dd>正式首发按来源核实，未公布时留空</dd>
          </div>
          <div>
            <dt>伤停 / xG</dt>
            <dd>暂无已接入的可靠完整来源</dd>
          </div>
          <div>
            <dt>AI 解读</dt>
            <dd>
              {{
                data?.ai_configured
                  ? "已配置 · 仅点击后调用"
                  : "尚未配置 · 基线预测独立可用"
              }}
            </dd>
          </div>
          <div>
            <dt>日期与赛事</dt>
            <dd>持续采集并归档，支持历史与未来赛程</dd>
          </div>
        </dl>
      </section>
    </div></template
  >
  <template v-else-if="tab === 'models'"
    ><section class="panel">
      <div class="panel-heading"><h2>计算引擎注册表</h2></div>
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
        empty="无法读取模型注册表"
      />
      <div class="section-note">
        近期样本不足时使用市场基线。模型估计与实际命中率是不同指标，不展示未经验证的胜率。
      </div>
    </section></template
  >
  <template v-else
    ><section class="panel">
      <div class="panel-heading"><h2>训练与评估准备</h2></div>
      <div class="inline-notice">
        当前基线参数未通过历史训练拟合。系统正在积累真实赛前预测与赛果，不提供虚构训练任务。
      </div>
      <div class="evaluation-rules">
        <div>
          <strong>数据集</strong>
          <p>赛前快照与最终赛果关联；缺失、取消和异常比赛单独处理。</p>
        </div>
        <div>
          <strong>时间切分</strong>
          <p>先用历史数据训练，再向后验证，禁止将赛后信息用作赛前特征。</p>
        </div>
        <div>
          <strong>验证后启用</strong>
          <p>
            对照市场基线，评估 Log Loss、Brier
            和校准误差。尚无结果时不填假指标。
          </p>
        </div>
      </div>
    </section></template
  >
</template>
