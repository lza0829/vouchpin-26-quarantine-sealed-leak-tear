<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const camps = ref([])
const form = ref({ event_type: 'redeem', coupon_code: '', campaign_code: 'NEW10', amount: '' })
const msg = ref('')
const err = ref('')

async function load() {
  err.value = ''
  try {
    camps.value = await api.campaigns()
    rows.value = await api.events()
    if (!form.value.campaign_code && camps.value[0]) form.value.campaign_code = camps.value[0].code
  } catch (e) { err.value = e.message }
}
async function submit() {
  msg.value = ''; err.value = ''
  try {
    const body = { ...form.value }
    if (body.amount === '') delete body.amount
    else body.amount = Number(body.amount)
    const r = await api.addEvent(body)
    if (r.status === 'quarantined') {
      msg.value = `事件 #${r.event_id} 已入隔离区 #${r.quarantine_id} · ${r.reason_code}：${r.reason}`
    } else {
      msg.value = `已写入事件 #${r.event_id}` + (r.fact_id ? ` / 事实 #${r.fact_id}` : '')
    }
    form.value.coupon_code = ''
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="row">
      <label>类型
        <select v-model="form.event_type">
          <option value="issue">发放</option>
          <option value="redeem">核销</option>
          <option value="void">作废</option>
        </select>
      </label>
      <label>券码 <input v-model="form.coupon_code" placeholder="C-2xxx" /></label>
      <label>活动
        <select v-model="form.campaign_code">
          <option v-for="c in camps" :key="c.code" :value="c.code">{{ c.code }} · {{ c.title }}</option>
        </select>
      </label>
      <label>金额 <input v-model="form.amount" placeholder="可空=面额" /></label>
      <button type="button" @click="submit">写入事件</button>
      <button type="button" class="ghost" @click="load">刷新</button>
    </div>
    <p class="msg" v-if="!err">非法核销（无发放·金额非正·活动不存在等）不写事实，转入隔离区并可查原因码。</p>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>券码</th><th>活动</th><th>金额</th><th>业务日</th><th>时刻</th><th>去向</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.id }}</td>
          <td><span class="tag" :class="{ warn: r.event_type==='redeem' }">{{ r.event_type }}</span></td>
          <td>{{ r.coupon_code }}</td>
          <td>{{ r.campaign_code }}</td>
          <td>{{ r.amount }}</td>
          <td>{{ r.business_day }}</td>
          <td>{{ r.occurred_at }}</td>
          <td>
            <span v-if="r.quarantine_id" class="tag warn">隔离 {{ r.quarantine_status }}</span>
            <span v-else-if="r.reason_code" class="tag warn">{{ r.reason_code }}</span>
            <span v-else-if="r.event_type==='redeem'" class="tag ok">已入事实</span>
            <span v-else class="muted">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
