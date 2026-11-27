/** 干燥补测条与精制领取单 API 客户端。 */
import { request } from './client'

const API = '/api'

export interface MoistureRetest {
  id: number
  干燥记录ID: number
  干燥确认编号: string
  批次号: string
  炉次: string
  盘号: string
  含水率: number
  实测序号: number
  是否改正: boolean
  状态: string
  有效: boolean
  登记人: string
  备注: string
  替代了: number | null
  被替代为: number | null
}

export interface GradingPickup {
  id: number
  批次号: string
  分级记录ID: number
  炉次: string
  盘号: string
  补测条ID: number
  含水率: number
  干燥记录ID: number | null
  干燥确认编号: string
  状态: string
  领取次数: number
  重试原因: string[]
  重核原因: string
  签收含水率: number | null
  实际使用补测条ID?: number | null
  实际使用含水率?: number | null
  读数状态?: string
  使用的补测条?: MoistureRetest | null
  补测条是否仍有效?: boolean
  接入的干燥确认?: Record<string, any> | null
}

export interface PanContext {
  炉次: string
  盘号: string
  有效补测条: MoistureRetest | null
  历次实测: MoistureRetest[]
  接过的干燥确认: Record<string, any>[]
  领取单: GradingPickup[]
  未正式办理: GradingPickup[]
  新读数能否作用于本次分级: boolean
  旧分级处置提示: string
}

async function postJson<T>(path: string, body: Record<string, any>): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload?.detail || `接口返回 ${response.status}`)
  }
  return payload as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload?.detail || `接口返回 ${response.status}`)
  }
  return payload as T
}

export async function createMoistureRetest(
  dryingEntryId: number,
  values: Record<string, any>,
): Promise<{ ok: boolean; message: string; retest: MoistureRetest }> {
  return postJson(`${API}/moisture-retests`, {
    ...values,
    drying_entry_id: dryingEntryId,
  })
}

export async function fetchRetests(
  furnace: string,
  pan: string,
): Promise<{ items: MoistureRetest[]; effective: MoistureRetest | null }> {
  const params = new URLSearchParams({ furnace, pan })
  return getJson(`${API}/moisture-retests?${params.toString()}`)
}

export async function fetchPanContext(
  furnace: string,
  pan: string,
  gradingEntryId?: number,
  batchNo?: string,
): Promise<PanContext> {
  const params = new URLSearchParams({ furnace, pan })
  if (gradingEntryId) params.set('grading_entry_id', String(gradingEntryId))
  if (batchNo) params.set('batch_no', batchNo)
  return (await getJson<{ detail: PanContext }>(
    `${API}/moisture-retests/pan-context?${params.toString()}`,
  )).detail
}

export async function createGradingPickup(
  gradingEntryId: number,
  values: Record<string, any>,
): Promise<{ ok: boolean; message: string; pickup: GradingPickup }> {
  return postJson(`${API}/grading-pickups`, {
    ...values,
    grading_entry_id: gradingEntryId,
  })
}

export async function signGradingPickup(
  pickupId: number,
): Promise<{ ok: boolean; message: string; pickup: GradingPickup }> {
  return postJson(`${API}/grading-pickups/${pickupId}/sign`, {})
}

export async function resolveGradingPickup(
  pickupId: number,
  decision: string,
  reason = '',
): Promise<{ ok: boolean; message: string; pickup: GradingPickup }> {
  return postJson(`${API}/grading-pickups/${pickupId}/resolve`, { decision, reason })
}
