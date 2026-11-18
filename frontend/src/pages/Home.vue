<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const d = ref(null)
const err = ref('')
async function load() {
  err.value = ''
  try { d.value = await api.dashboard() } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ d.business_day }}</div></div>
      <div class="stat"><div class="k">今日事件</div><div class="v">{{ d.event_count }}</div></div>
      <div class="stat"><div class="k">核销事实</div><div class="v ticket">{{ d.fact_count }}</div></div>
      <div class="stat"><div class="k">待钉 open</div><div class="v">{{ d.open_fact_count }}</div></div>
      <div class="stat"><div class="k">今日核销额</div><div class="v ticket">{{ d.redeem_total_today }}</div></div>
      <div class="stat"><div class="k">日已钉扎</div><div class="v">{{ d.day_sealed ? '是' : '否' }}</div></div>
      <div class="stat"><div class="k">隔离仍在</div><div class="v">{{ d.quarantine_count }}</div></div>
      <div class="stat"><div class="k">非法核销</div><div class="v">{{ d.illegal_redeem_count }}</div></div>
      <div class="stat"><div class="k">隔离待处理</div><div class="v ticket">{{ d.quarantine_count }}</div></div>
      <div class="stat"><div class="k">同键双栖</div><div class="v">{{ d.dual_home_event_count }}</div></div>
      <div class="stat"><div class="k">无处安放</div><div class="v">{{ d.homeless_event_count }}</div></div>
    </div>
    <p class="msg" v-if="d && !err">
      票根洋红标出核销侧数字。非法核销一律进隔离区：不计入核销额、钉扎合计与预览，只能晋升（重新校验通过）或丢弃，且均留审计。
      待处理 {{ d.quarantine_count }} 笔 · 金额 {{ d.quarantine_amount }}；已晋升
      {{ d.quarantine_promoted_count }}、已丢弃 {{ d.quarantine_discarded_count }}。日钉扎后迟到的核销同样只进隔离区，不得晋升。
      每笔核销事件只有一个归属：正式事实 {{ d.fact_count }} + 隔离未清 {{ d.quarantine_unpromoted_count }}
      = 事件侧非法 {{ d.illegal_redeem_count }} + 合法 {{ d.legal_redeem_count }} =
      {{ d.formal_plus_quarantine }} / {{ d.event_illegal_plus_legal }}；同键双栖 {{ d.dual_home_event_count }} 笔、无处安放 {{ d.homeless_event_count }} 笔应为 0。
    </p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
</template>
