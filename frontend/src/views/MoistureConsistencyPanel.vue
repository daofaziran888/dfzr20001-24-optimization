<template>
  <section class="consistency-panel" :data-testid="`consistency-${moduleKey}`">
    <header class="panel-head">
      <div>
        <h3>{{ isDrying ? '含水率补测条' : '精制领取与补测查回' }}</h3>
        <p class="panel-desc">
          {{ isDrying
            ? '每次补测=本炉本盘的一次真实实测；只作废同盘上一条读数，不影响同炉其它盘。'
            : '每张领取单只认本盘当前唯一有效补测条；已签收的料不会重复领，也不会被撤掉。' }}
        </p>
      </div>
      <div class="panel-scope" v-if="selectedEntry">
        当前记录 #{{ selectedEntry.id }}
        <button class="link" type="button" @click="$emit('clear-entry')">清除</button>
      </div>
    </header>

    <!-- 干燥：补测条列表 + 登记 -->
    <div v-if="isDrying" class="panel-body">
      <form class="inline-form" @submit.prevent="submitRetest">
        <label>
          <span>干燥记录ID</span>
          <input v-model.number="retestForm.drying_entry_id" type="number" min="1"
            :data-testid="'retest-drying-id'" />
        </label>
        <label>
          <span>炉次</span>
          <input v-model="retestForm.炉次" data-testid="retest-furnace" placeholder="如 L-2026-11-A" />
        </label>
        <label>
          <span>盘号</span>
          <input v-model="retestForm.盘号" data-testid="retest-pan" placeholder="如 3" />
        </label>
        <label>
          <span>实测含水率(%)</span>
          <input v-model="retestForm.含水率" data-testid="retest-moisture" placeholder="如 6.4" />
        </label>
        <label>
          <span>登记人</span>
          <input v-model="retestForm.登记人" data-testid="retest-operator" />
        </label>
        <label class="wide">
          <span>备注</span>
          <input v-model="retestForm.备注" data-testid="retest-note" />
        </label>
        <button class="btn primary" data-testid="submit-retest" type="submit" :disabled="busy">
          {{ busy ? '提交中…' : '提交补测' }}
        </button>
      </form>

      <div class="query-row">
        <label>
          <span>按炉次查</span>
          <input v-model="queryForm.炉次" data-testid="query-furnace" />
        </label>
        <label>
          <span>按盘号查</span>
          <input v-model="queryForm.盘号" data-testid="query-pan" />
        </label>
        <button class="btn" type="button" data-testid="query-retests" @click="loadRetests">查询补测条</button>
      </div>

      <p v-if="message" :class="['panel-msg', ok ? 'ok' : 'bad']" data-testid="retest-message">{{ message }}</p>

      <table v-if="retests.length" class="sub-table" data-testid="retest-table">
        <thead>
          <tr>
            <th>补测条</th><th>炉次</th><th>盘号</th><th>含水率</th>
            <th>第几次实测</th><th>接入干燥确认</th><th>状态</th><th>替代关系</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in retests" :key="r.id" :data-testid="`retest-row-${r.id}`">
            <td>#{{ r.id }}</td>
            <td>{{ r.炉次 }}</td>
            <td>{{ r.盘号 }}</td>
            <td>{{ r.含水率 }}%</td>
            <td>{{ r.实测序号 }}</td>
            <td>#{{ r.干燥记录ID }} {{ r.干燥确认编号 }}</td>
            <td>
              <span :class="['tag', r.有效 ? 'ok' : 'void']">{{ r.状态 }}</span>
            </td>
            <td>
              <span v-if="r.替代了">替代 #{{ r.替代了 }}</span>
              <span v-else-if="r.被替代为">被 #{{ r.被替代为 }} 替代</span>
              <span v-else>—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 分级：领取 + 本盘上下文 -->
    <div v-else class="panel-body">
      <form class="inline-form" @submit.prevent="submitPickup">
        <label>
          <span>分级记录ID</span>
          <input v-model.number="pickupForm.grading_entry_id" type="number" min="1"
            :data-testid="'pickup-grading-id'" />
        </label>
        <label>
          <span>炉次</span>
          <input v-model="pickupForm.炉次" data-testid="pickup-furnace" />
        </label>
        <label>
          <span>盘号</span>
          <input v-model="pickupForm.盘号" data-testid="pickup-pan" />
        </label>
        <label class="wide">
          <span>重试原因（重新领取时必填）</span>
          <input v-model="pickupForm.重试原因" data-testid="pickup-retry-reason" />
        </label>
        <button class="btn primary" data-testid="submit-pickup" type="submit" :disabled="busy">
          {{ busy ? '处理中…' : '领出本盘 / 重提确认' }}
        </button>
        <button class="btn" type="button" data-testid="load-pan-context" @click="loadPanContext">
          查本盘补测与领取
        </button>
      </form>

      <p v-if="message" :class="['panel-msg', ok ? 'ok' : 'bad']" data-testid="pickup-message">{{ message }}</p>

      <div v-if="panContext" class="pan-context" data-testid="pan-context">
        <div class="context-grid">
          <div>
            <h4>接过的干燥确认</h4>
            <ul v-if="panContext.接过的干燥确认.length">
              <li v-for="d in panContext.接过的干燥确认" :key="d.id">
                #{{ d.id }} {{ d.登记编号 }} <span class="muted">({{ d.status }})</span>
              </li>
            </ul>
            <p v-else class="muted">尚未接过干燥确认</p>
          </div>
          <div>
            <h4>本盘历次实测</h4>
            <ul v-if="panContext.历次实测.length">
              <li v-for="r in panContext.历次实测" :key="r.id">
                第{{ r.实测序号 }}次 {{ r.含水率 }}%
                <span class="muted">补测条#{{ r.id }}</span>
                <span :class="['tag', r.有效 ? 'ok' : 'void']">{{ r.状态 }}</span>
              </li>
            </ul>
            <p v-else class="muted">本盘暂无实测</p>
          </div>
          <div>
            <h4>新读数作用</h4>
            <p>
              {{ panContext.有效补测条
                ? `当前有效：补测条#${panContext.有效补测条.id} ${panContext.有效补测条.含水率}%`
                : '本盘无有效补测条' }}
            </p>
            <p :class="panContext.新读数能否作用于本次分级 ? 'ok-text' : 'muted'">
              {{ panContext.新读数能否作用于本次分级 ? '可作用于本次新分级' : '不能作用于本次分级' }}
            </p>
            <p class="hint">{{ panContext.旧分级处置提示 }}</p>
          </div>
        </div>

        <table v-if="panContext.领取单.length" class="sub-table" data-testid="pickup-table">
          <thead>
            <tr>
              <th>领取单</th><th>盘</th><th>使用补测条</th><th>含水率</th>
              <th>状态</th><th>重试原因</th><th>处理</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in panContext.领取单" :key="p.id" :data-testid="`pickup-row-${p.id}`">
              <td>#{{ p.id }} <span class="muted">第{{ p.领取次数 }}次</span></td>
              <td>{{ p.炉次 }}/{{ p.盘号 }}</td>
              <td>
                #{{ p.补测条ID }}
                <span v-if="p.状态 !== '已签收' && p.补测条是否仍有效 === false" class="muted">（已作废）</span>
              </td>
              <td>
                <span v-if="p.状态 === '已签收'">
                  实际使用 {{ p.实际使用含水率 ?? p.签收含水率 ?? p.含水率 }}%
                  <span class="muted">补测条#{{ p.实际使用补测条ID ?? p.补测条ID }}</span>
                  <div v-if="p.读数状态" class="recheck-reason">{{ p.读数状态 }}</div>
                </span>
                <span v-else>{{ p.含水率 }}%</span>
              </td>
              <td><span :class="['tag', statusClass(p.状态)]">{{ p.状态 }}</span></td>
              <td>
                <span v-if="p.重试原因 && p.重试原因.length">{{ p.重试原因.join('；') }}</span>
                <span v-else class="muted">—</span>
                <div v-if="p.重核原因" class="recheck-reason" data-testid="recheck-reason">{{ p.重核原因 }}</div>
              </td>
              <td class="pickup-actions">
                <button v-if="p.状态 === '待入秤确认'" class="link"
                  :data-testid="`sign-${p.id}`" @click="signPickup(p.id)">入秤签收</button>
                <template v-if="p.状态 === '需重核'">
                  <button class="link" :data-testid="`continue-${p.id}`"
                    @click="resolvePickup(p.id, '继续按原交接')">继续按原交接</button>
                  <button class="link" :data-testid="`recheck-${p.id}`"
                    @click="resolvePickup(p.id, '按新读数重核')">按新读数重核</button>
                </template>
                <span v-if="p.状态 === '已签收'" class="signed-note">实际已领料，不再变动</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">本盘暂无领取单</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  createGradingPickup,
  createMoistureRetest,
  fetchPanContext,
  fetchRetests,
  resolveGradingPickup,
  signGradingPickup,
  type MoistureRetest,
  type PanContext,
} from '@/api/moisture-client'
import type { EntryRow } from '@/data/types'

const props = defineProps<{
  moduleKey: string
  selectedEntry: EntryRow | null
}>()
const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'clear-entry'): void
}>()

const isDrying = computed(() => props.moduleKey === 'drying')
const busy = ref(false)
const ok = ref(false)
const message = ref('')
const retests = ref<MoistureRetest[]>([])
const panContext = ref<PanContext | null>(null)

const retestForm = ref({
  drying_entry_id: 0,
  炉次: '',
  盘号: '',
  含水率: '',
  登记人: '',
  备注: '',
})
const queryForm = ref({ 炉次: '', 盘号: '' })
const pickupForm = ref({
  grading_entry_id: 0,
  炉次: '',
  盘号: '',
  重试原因: '',
})

watch(
  () => props.selectedEntry,
  (row) => {
    if (!row) return
    if (isDrying.value) {
      retestForm.value.drying_entry_id = Number(row.id)
      retestForm.value.炉次 = String(row['炉次'] ?? '')
      queryForm.value.炉次 = String(row['炉次'] ?? '')
      void loadRetests()
    } else {
      pickupForm.value.grading_entry_id = Number(row.id)
      pickupForm.value.炉次 = String(row['炉次'] ?? '')
      pickupForm.value.盘号 = String(row['盘号'] ?? '')
    }
  },
  { immediate: true },
)

function statusClass(status: string): string {
  if (status === '已签收') return 'ok'
  if (status === '需重核') return 'warn'
  return 'pending'
}

function flash(success: boolean, text: string) {
  ok.value = success
  message.value = text
}

async function loadRetests() {
  const { 炉次, 盘号 } = queryForm.value
  if (!炉次 || !盘号) {
    flash(false, '查询补测条需同时填写炉次与盘号')
    return
  }
  try {
    const payload = await fetchRetests(炉次, 盘号)
    retests.value = payload.items || []
    flash(true, `炉次 ${炉次} 盘号 ${盘号} 共 ${retests.value.length} 条实测`)
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '查询失败')
  }
}

async function submitRetest() {
  busy.value = true
  flash(false, '')
  try {
    const result = await createMoistureRetest(retestForm.value.drying_entry_id, {
      炉次: retestForm.value.炉次,
      盘号: retestForm.value.盘号,
      含水率: retestForm.value.含水率,
      登记人: retestForm.value.登记人,
      备注: retestForm.value.备注,
    })
    flash(true, result.message)
    queryForm.value = { 炉次: retestForm.value.炉次, 盘号: retestForm.value.盘号 }
    await loadRetests()
    emit('changed')
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '补测提交失败')
  } finally {
    busy.value = false
  }
}

async function submitPickup() {
  busy.value = true
  flash(false, '')
  try {
    const result = await createGradingPickup(pickupForm.value.grading_entry_id, {
      炉次: pickupForm.value.炉次,
      盘号: pickupForm.value.盘号,
      重试原因: pickupForm.value.重试原因,
    })
    flash(true, result.message)
    await loadPanContext()
    emit('changed')
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '领取失败')
  } finally {
    busy.value = false
  }
}

async function loadPanContext() {
  const { 炉次, 盘号 } = pickupForm.value
  if (!炉次 || !盘号) {
    flash(false, '查本盘需填写炉次与盘号')
    return
  }
  try {
    panContext.value = await fetchPanContext(
      炉次, 盘号, pickupForm.value.grading_entry_id || undefined,
    )
    flash(true, `已加载炉次 ${炉次} 盘号 ${盘号} 的补测与领取情况`)
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '查询失败')
  }
}

async function signPickup(pickupId: number) {
  try {
    const result = await signGradingPickup(pickupId)
    flash(true, result.message)
    await loadPanContext()
    emit('changed')
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '签收失败')
  }
}

async function resolvePickup(pickupId: number, decision: string) {
  try {
    const result = await resolveGradingPickup(pickupId, decision)
    flash(true, result.message)
    await loadPanContext()
    emit('changed')
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '重核处理失败')
  }
}
</script>

<style scoped>
.consistency-panel {
  margin: 12px 0 18px;
  padding: 14px 16px;
  background: #f8fbff;
  border: 1px solid var(--border);
  border-left: 3px solid var(--brand);
  border-radius: 8px;
}
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.panel-head h3 { margin: 0; font-size: 15px; }
.panel-desc { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.panel-scope { font-size: 12px; color: var(--muted); white-space: nowrap; }
.inline-form, .query-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-top: 12px; }
.inline-form label, .query-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.inline-form label.wide { flex: 1; min-width: 160px; }
.inline-form input, .query-row input {
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; min-width: 120px;
}
.panel-msg { margin: 10px 0 0; font-size: 13px; }
.panel-msg.ok { color: #15803d; }
.panel-msg.bad { color: #b42318; }
.sub-table { width: 100%; border-collapse: collapse; margin-top: 12px; background: #fff; }
.sub-table th, .sub-table td { border: 1px solid var(--border); padding: 6px 8px; font-size: 12px; text-align: left; vertical-align: top; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; }
.tag.ok { background: #dcfce7; color: #166534; }
.tag.void { background: #f1f5f9; color: #94a3b8; }
.tag.warn { background: #fef3c7; color: #92400e; }
.tag.pending { background: #e0edff; color: #1d4ed8; }
.context-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px; }
.context-grid h4 { margin: 0 0 6px; font-size: 13px; }
.context-grid ul { margin: 0; padding-left: 16px; font-size: 12px; }
.context-grid li { margin-bottom: 4px; }
.muted { color: var(--muted); }
.hint { font-size: 12px; color: #92400e; }
.ok-text { color: #15803d; font-size: 12px; }
.recheck-reason { margin-top: 4px; color: #92400e; font-size: 11px; }
.pickup-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.signed-note { color: var(--muted); font-size: 11px; }
</style>
