<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const audit = ref([])
const day = ref('')
const msg = ref('')
const err = ref('')
const busy = ref(null)
const edit = ref({})

async function load() {
  err.value = ''
  try {
    const d = await api.getDay()
    day.value = d.business_day
    rows.value = await api.quarantine(day.value)
    const next = {}
    for (const r of rows.value) {
      // keep a decision draft if the operator already started editing it
      next[r.id] = edit.value[r.id] || { amount: r.amount, campaign_code: r.campaign_code, note: '' }
    }
    edit.value = next
    audit.value = await api.audit('quarantine')
  } catch (e) { err.value = e.message }
}

function auditFor(id) {
  return audit.value.filter((a) => a.target_id === id)
}

async function promote(r) {
  msg.value = ''; err.value = ''; busy.value = r.id
  try {
    const e = edit.value[r.id] || {}
    const res = await api.promote(r.id, {
      amount: e.amount === '' || e.amount == null ? undefined : Number(e.amount),
      campaign_code: e.campaign_code || undefined,
      note: e.note || '',
    })
    msg.value = `隔离 #${r.id} 已晋升为核销事实 #${res.fact_id}`
    await load()
  } catch (e) { err.value = e.message } finally { busy.value = null }
}

async function discard(r) {
  msg.value = ''; err.value = ''
  if (!window.confirm(`确认丢弃隔离 #${r.id}？丢弃后不可再晋升。`)) return
  busy.value = r.id
  try {
    const e = edit.value[r.id] || {}
    await api.discard(r.id, { note: e.note || '' })
    msg.value = `隔离 #${r.id} 已丢弃`
    await load()
  } catch (e) { err.value = e.message } finally { busy.value = null }
}

onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="row">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ day }}</div></div>
      <div class="stat"><div class="k">待处理隔离</div><div class="v ticket">{{ rows.filter(r => r.status === 'quarantined').length }}</div></div>
      <button type="button" class="ghost" @click="load">刷新</button>
    </div>
    <p class="msg" v-if="!err">隔离行不进 open 事实、不进钉扎合计与预览；晋升须重新通过校验，晋升/丢弃均记审计。</p>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>

  <div class="panel">
    <table>
      <thead>
        <tr>
          <th>隔离ID</th><th>券码</th><th>活动</th><th>金额</th><th>原因码</th><th>状态</th><th>事实</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.id }}</td>
          <td>{{ r.coupon_code }}</td>
          <td>{{ r.campaign_code }}</td>
          <td>{{ r.amount }}</td>
          <td>
            <span class="tag warn">{{ r.reason_code }}</span>
            <small class="reason">{{ r.reason_label }}</small>
          </td>
          <td>
            <span class="tag" :class="{ ok: r.status === 'promoted', warn: r.status === 'quarantined' }">{{ r.status }}</span>
            <small v-if="r.decision_note" class="reason">{{ r.decision_note }}</small>
          </td>
          <td>{{ r.fact_id || '—' }}</td>
          <td>
            <template v-if="r.status === 'quarantined'">
              <div class="act">
                <input v-model="edit[r.id].amount" class="mini" placeholder="金额" />
                <input v-model="edit[r.id].campaign_code" class="mini" placeholder="活动" />
                <input v-model="edit[r.id].note" class="mini" placeholder="备注" />
              </div>
              <div class="act">
                <button type="button" :disabled="busy === r.id" @click="promote(r)">晋升</button>
                <button type="button" class="ghost" :disabled="busy === r.id" @click="discard(r)">丢弃</button>
              </div>
            </template>
            <span v-else class="muted">—</span>
            <ul class="trail" v-if="auditFor(r.id).length">
              <li v-for="a in auditFor(r.id)" :key="a.id">
                {{ a.action }} · {{ a.created_at }}<span v-if="a.detail && a.detail.note"> · {{ a.detail.note }}</span>
              </li>
            </ul>
          </td>
        </tr>
      </tbody>
    </table>
    <p class="msg" v-if="!rows.length">今日无隔离行。</p>
  </div>
</template>
