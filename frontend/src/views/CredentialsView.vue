<script setup>
import { computed, ref } from "vue";
import { RefreshCw, RotateCcw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import AuthorizationPanel from "../components/credentials/AuthorizationPanel.vue";
import RoutingSummary from "../components/credentials/RoutingSummary.vue";
import RoutingTimeline from "../components/credentials/RoutingTimeline.vue";
import CredentialTable from "../components/credentials/CredentialTable.vue";
import RequestDetails from "../components/requests/RequestDetails.vue";
import CredentialTest from "../components/credentials/CredentialTest.vue";
import WorkbuddyPanel from "../components/credentials/WorkbuddyPanel.vue";
import { useResource } from "../composables/useResource";
import { useAction } from "../composables/useAction";
import { consoleApi } from "../services/console";
import { credentialLabel } from "../lib/format";
const tab = ref("codebuddy");
const testing = ref(null);
const selectedRequest = ref(null);
const { data, loading, error, refresh } = useResource(
  consoleApi.credentials,
  {
    credentials: [],
    events: [],
    mode: "auto",
    available: 0,
    cooldown_seconds: 300,
  },
  5000,
);
const { busy, run } = useAction();
const search = ref(""),
  state = ref("");
const filtered = computed(() => Boolean(search.value || state.value));
function resetFilters() {
  search.value = "";
  state.value = "";
}
const rows = computed(() =>
  data.value.credentials.filter(
    (row) =>
      (!state.value || row.state === state.value) &&
      `${credentialLabel(row)} ${row.filename}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
  ),
);
function mode(value, filename) {
  run(
    async () => {
      await consoleApi.setMode(value, filename);
      await refresh();
    },
    value === "auto" ? "已恢复自动切换" : "已锁定凭证",
  );
}
function remove(row) {
  if (
    !window.confirm(
      `删除凭证 ${credentialLabel(row)}？此操作会删除服务器上的凭证文件。`,
    )
  )
    return;
  run(async () => {
    await consoleApi.deleteCredential(row.filename);
    await refresh();
  }, "凭证已删除");
}
</script>
<template>
  <section class="view-stack">
    <PageHeader
      title="凭证管理"
      eyebrow="CREDENTIALS"
      description="管理账号授权，让每一次请求都有清晰的去向。"
      ><template v-if="tab === 'codebuddy'"
        ><button class="button secondary" :disabled="loading" @click="refresh">
          <RefreshCw :size="16" :class="{ spin: loading }" />刷新</button
        ><AuthorizationPanel product="codebuddy" @saved="refresh" /></template
    ></PageHeader>
    <div class="tabs" role="tablist" aria-label="凭证产品">
      <button
        id="codebuddy-tab"
        :class="{ active: tab === 'codebuddy' }"
        role="tab"
        :aria-selected="tab === 'codebuddy'"
        @click="tab = 'codebuddy'"
      >
        CodeBuddy <span>{{ data.credentials.length }}</span></button
      ><button
        id="workbuddy-tab"
        :class="{ active: tab === 'workbuddy' }"
        role="tab"
        :aria-selected="tab === 'workbuddy'"
        @click="tab = 'workbuddy'"
      >
        WorkBuddy 签到
      </button>
    </div>
    <template v-if="tab === 'codebuddy'">
      <ResourceNotice :error="error" @retry="refresh" />
      <RoutingSummary :routing="data" :busy="busy" @mode="mode" />
      <div class="credential-layout">
        <section class="data-section credential-pool">
          <header>
            <div>
              <h3>
                凭证池 <span class="count">{{ data.credentials.length }}</span>
              </h3>
              <small>{{ data.available }} 个可用 · 每 5 秒自动更新</small>
            </div>
          </header>
          <div class="table-toolbar credential-filters">
            <input
              v-model="search"
              type="search"
              placeholder="搜索账号或凭证文件"
              aria-label="搜索凭证"
            /><select v-model="state" aria-label="凭证状态">
              <option value="">全部状态</option>
              <option value="available">可用</option>
              <option value="refreshable">可自动续期</option>
              <option value="cooldown">冷却中</option>
              <option value="reauthorize">需要重新授权</option>
              <option value="expired">已过期</option>
            </select>
            <button
              class="button secondary"
              :disabled="!filtered"
              @click="resetFilters"
            >
              <RotateCcw :size="15" />重置
            </button>
          </div>
          <div v-if="loading && !data.credentials.length" class="empty-state">
            正在加载凭证…
          </div>
          <CredentialTable
            v-else
            :rows="rows"
            :busy="busy"
            :server-time="data.server_time"
            :filtered="filtered"
            @lock="mode('manual', $event.filename)"
            @delete="remove"
            @test="testing = $event"
            @saved="refresh"
            @reset="resetFilters"
          />
        </section>
        <RoutingTimeline
          :events="data.events"
          @request="selectedRequest = $event"
          :cooldown-seconds="data.cooldown_seconds"
        />
      </div>
    </template>
    <WorkbuddyPanel v-else />
    <RequestDetails
      v-if="selectedRequest"
      :id="selectedRequest"
      :key="selectedRequest"
      @close="selectedRequest = null"
    />
    <CredentialTest
      v-if="testing"
      :credential="testing"
      @close="
        testing = null;
        refresh();
      "
    />
  </section>
</template>
