<script setup>
import { LockKeyhole, Save } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import ResourceNotice from "../components/ResourceNotice.vue";
import { useSettings } from "../composables/useSettings";
const {
  settings,
  labels,
  readonly,
  loading,
  saving,
  error,
  dirty,
  save,
  reset,
  load,
} = useSettings();
const groups = [
  {
    title: "访问与站点",
    description: "访问密码用于管理端登录及 API Bearer 认证。",
    keys: ["CODEBUDDY_PASSWORD", "CODEBUDDY_SITE"],
  },
  {
    title: "自动签到",
    description: "使用 WorkBuddy 凭证领取奖励，执行时间采用北京时间。",
    keys: [
      "CODEBUDDY_AUTO_CHECKIN",
      "CODEBUDDY_CHECKIN_TIME",
      "CODEBUDDY_BARK_URL",
    ],
  },
  {
    title: "部署信息",
    description: "这些参数由服务器环境配置管理，修改后需要重启服务。",
    keys: [
      "CODEBUDDY_HOST",
      "CODEBUDDY_PORT",
      "CODEBUDDY_CREDS_DIR",
      "WORKBUDDY_CREDS_DIR",
      "CODEBUDDY_LOG_LEVEL",
    ],
  },
];
</script>
<template>
  <section class="view-stack settings-view">
    <PageHeader
      title="服务设置"
      eyebrow="SETTINGS"
      description="配置访问方式与日常任务。"
    />
    <ResourceNotice :error="error" @retry="load" />
    <div v-if="loading" class="empty-state">正在读取设置…</div>
    <form
      v-else-if="Object.keys(settings).length"
      class="view-stack"
      @submit.prevent="save"
    >
      <section
        v-for="group in groups"
        :key="group.title"
        class="data-section settings-section"
      >
        <header>
          <div>
            <h3>{{ group.title }}</h3>
            <p class="page-description">{{ group.description }}</p>
          </div>
          <LockKeyhole v-if="group.title === '部署信息'" :size="18" />
        </header>
        <div class="settings-form">
          <label v-for="key in group.keys" :key="key" class="setting-row"
            ><span
              ><strong>{{ labels[key] || key }}</strong
              ><small>{{ key }}</small></span
            >
            <div class="setting-control">
              <select
                v-if="key === 'CODEBUDDY_SITE'"
                v-model="settings[key]"
                :disabled="saving"
              >
                <option value="china">国内站</option>
                <option value="international">国际站</option></select
              ><select
                v-else-if="key === 'CODEBUDDY_AUTO_CHECKIN'"
                v-model="settings[key]"
                :disabled="saving"
              >
                <option value="true">开启</option>
                <option value="false">关闭</option></select
              ><input
                v-else
                v-model="settings[key]"
                :disabled="readonly.includes(key) || saving"
                :required="
                  ['CODEBUDDY_PASSWORD', 'CODEBUDDY_CHECKIN_TIME'].includes(key)
                "
                :type="
                  key === 'CODEBUDDY_PASSWORD'
                    ? 'password'
                    : key === 'CODEBUDDY_CHECKIN_TIME'
                      ? 'time'
                      : key === 'CODEBUDDY_BARK_URL'
                        ? 'url'
                        : 'text'
                "
                :autocomplete="
                  key === 'CODEBUDDY_PASSWORD' ? 'new-password' : 'off'
                "
                :placeholder="
                  key === 'CODEBUDDY_BARK_URL' ? '留空则不推送' : ''
                "
              /></div
          ></label>
        </div>
      </section>
      <footer class="save-bar">
        <span :class="{ 'error-text': dirty }">{{
          dirty ? "有未保存的修改" : "所有设置已保存"
        }}</span>
        <div class="toolbar-actions">
          <button
            type="button"
            class="button secondary"
            :disabled="!dirty || saving"
            @click="reset"
          >
            放弃修改</button
          ><button class="button primary" :disabled="!dirty || saving">
            <Save :size="16" />{{ saving ? "保存中…" : "保存设置" }}
          </button>
        </div>
      </footer>
    </form>
  </section>
</template>
