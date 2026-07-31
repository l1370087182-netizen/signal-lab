import { formatPrice } from '../utils/format'
import type { LevelPoint, Levels, TradePlan } from '../api/client'

type Props = {
  levels: Levels
  tradePlan?: TradePlan | null
  isBuy: boolean
}

function LevelCell({
  label,
  point,
  tone,
}: {
  label: string
  point?: LevelPoint | null
  tone: 'sup' | 'res'
}) {
  if (!point?.price) {
    return (
      <div>
        <dt>{label}</dt>
        <dd>—</dd>
      </div>
    )
  }
  return (
    <div>
      <dt>
        {label}
        <span className={`lvl-tag ${point.strength === '强' ? 'strong' : 'weak'}`}>
          {point.strength}
        </span>
      </dt>
      <dd className={tone}>{formatPrice(point.price)}</dd>
    </div>
  )
}

export default function LevelsPanel({ levels, tradePlan, isBuy }: Props) {
  const unavailable = levels.available === false
  return (
    <section className="section levels-section">
      <h2>支撑 / 压力位</h2>
      {levels.note ? (
        <p className="msg muted">{levels.note}</p>
      ) : (
        <p className="msg muted">
          {levels.legend?.weak}；{levels.legend?.strong}
        </p>
      )}
      {unavailable ? (
        <p className="msg muted">可算指标与 AI 功能仍可用；样本充足后再显示结构位。</p>
      ) : null}
      <div className="levels-grid">
        <article className="level-card">
          <h3>短期</h3>
          <p className="level-horizon">{levels.short_term.horizon}</p>
          <dl className="level-kv four">
            <LevelCell label="弱支撑" point={levels.short_term.support?.weak} tone="sup" />
            <LevelCell label="强支撑" point={levels.short_term.support?.strong} tone="sup" />
            <LevelCell label="弱压力" point={levels.short_term.resistance?.weak} tone="res" />
            <LevelCell label="强压力" point={levels.short_term.resistance?.strong} tone="res" />
          </dl>
          <p className="ind-extra">{levels.short_term.basis}</p>
        </article>
        <article className="level-card">
          <h3>长期</h3>
          <p className="level-horizon">{levels.long_term.horizon}</p>
          <dl className="level-kv four">
            <LevelCell label="弱支撑" point={levels.long_term.support?.weak} tone="sup" />
            <LevelCell label="强支撑" point={levels.long_term.support?.strong} tone="sup" />
            <LevelCell label="弱压力" point={levels.long_term.resistance?.weak} tone="res" />
            <LevelCell label="强压力" point={levels.long_term.resistance?.strong} tone="res" />
          </dl>
          <p className="ind-extra">{levels.long_term.basis}</p>
        </article>
      </div>

      {isBuy && tradePlan && (
        <div className="trade-plan">
          <h3>买入方案价位</h3>
          <div className="trade-grid">
            <div className="trade-tile">
              <span>买入区间</span>
              <strong>
                {formatPrice(tradePlan.entry.low)} – {formatPrice(tradePlan.entry.high)}
              </strong>
              <em>{tradePlan.entry.note}</em>
            </div>
            <div className="trade-tile stop">
              <span>止损</span>
              <strong>{formatPrice(tradePlan.stop_loss.price)}</strong>
              <em>{tradePlan.stop_loss.note}</em>
            </div>
            <div className="trade-tile tp">
              <span>止盈 TP1</span>
              <strong>{formatPrice(tradePlan.take_profit.tp1)}</strong>
              <em>{tradePlan.take_profit.tp1_label || '弱压力附近'}</em>
            </div>
            <div className="trade-tile tp">
              <span>止盈 TP2</span>
              <strong>{formatPrice(tradePlan.take_profit.tp2)}</strong>
              <em>{tradePlan.take_profit.tp2_label || '强压力方向'}</em>
            </div>
            <div className="trade-tile">
              <span>支撑 弱/强（短）</span>
              <strong>
                {formatPrice(tradePlan.support.short_weak)} /{' '}
                {formatPrice(tradePlan.support.short_strong)}
              </strong>
            </div>
            <div className="trade-tile">
              <span>压力 弱/强（短）</span>
              <strong>
                {formatPrice(tradePlan.resistance.short_weak)} /{' '}
                {formatPrice(tradePlan.resistance.short_strong)}
              </strong>
            </div>
          </div>
          {tradePlan.risk_reward_tp1 != null && (
            <p className={`msg muted${tradePlan.risk_reward_tp1 < 1 ? ' rr-weak' : ''}`}>
              按回踩买入区间计：TP1 风险收益比约 1 : {tradePlan.risk_reward_tp1}
              {tradePlan.risk_reward_tp2 != null ? `；TP2 约 1 : ${tradePlan.risk_reward_tp2}` : ''}
              <span className="rr-explain">（止损风险归一为 1）</span>
              {tradePlan.risk_reward_note ? ` · ${tradePlan.risk_reward_note}` : ''}
            </p>
          )}
        </div>
      )}
    </section>
  )
}
