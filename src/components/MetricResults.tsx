import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Obj, formatMetric, resultMetrics } from "../lib";

export default function MetricResults({
  result,
  charts = false,
}: {
  result: Obj;
  charts?: boolean;
}) {
  return (
    <>
      {resultMetrics(result).map((metric) => {
        const changes = [
          ...metric.contributions,
          ...metric.category_contributions,
        ];
        const scale = metric.storage_unit === "人民币分" ? 100 : 1;
        const chartValue = (value: number) =>
          scale === 100
            ? `¥${value.toLocaleString("zh-CN")}`
            : `${value.toLocaleString("zh-CN")} ${metric.unit}`;
        return (
          <section
            className="metric-result"
            key={metric.metric}
            aria-label={metric.label}
          >
            <h3>{metric.label}</h3>
            <div className="result-numbers metric-numbers">
              <div>
                <small>本期{metric.label}</small>
                <b>{formatMetric(metric, metric.current)}</b>
              </div>
              <div>
                <small>基期{metric.label}</small>
                <b>{formatMetric(metric, metric.previous)}</b>
              </div>
              <div>
                <small>变化额（{metric.unit}）</small>
                <b className={(metric.delta ?? 0) < 0 ? "negative" : ""}>
                  {formatMetric(metric, metric.delta)}
                </b>
              </div>
              <div>
                <small>变化率</small>
                <b className={(metric.rate ?? 0) < 0 ? "negative" : ""}>
                  {metric.rate == null
                    ? "不适用"
                    : `${metric.rate > 0 ? "+" : ""}${metric.rate}%`}
                </b>
              </div>
            </div>
            {charts && metric.contributions.some((row) => row.delta) && (
              <>
                <h3>门店变化贡献（{metric.unit}）</h3>
                <div className="contribution-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={metric.contributions.map((row) => ({
                        name: row.name,
                        delta: (row.delta ?? 0) / scale,
                      }))}
                      layout="vertical"
                      margin={{ left: 0, right: 30 }}
                    >
                      <CartesianGrid strokeDasharray="3 4" horizontal={false} />
                      <XAxis
                        type="number"
                        tick={{ fontSize: 11 }}
                        tickFormatter={chartValue}
                      />
                      <YAxis
                        type="category"
                        dataKey="name"
                        width={90}
                        tick={{ fontSize: 12 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        formatter={(value: any) => [
                          chartValue(Number(value)),
                          `${metric.label}变化`,
                        ]}
                      />
                      <Bar
                        isAnimationActive={false}
                        dataKey="delta"
                        radius={[3, 3, 3, 3]}
                        barSize={20}
                      >
                        {metric.contributions.map((row, index) => (
                          <Cell
                            key={index}
                            fill={(row.delta ?? 0) < 0 ? "#b17a59" : "#4d7b57"}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </>
            )}
            <details className="contribution-data">
              <summary>门店排名与品类数据（{metric.unit}）</summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>维度</th>
                      <th>名称</th>
                      <th>本期{metric.label}</th>
                      {metric.previous != null && (
                        <>
                          <th>基期</th>
                          <th>变化额</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ...metric.rankings.map((row, index) => ({
                        ...row,
                        dimension: `门店 ${index + 1}`,
                      })),
                      ...metric.category_values.map((row) => ({
                        ...row,
                        dimension: "品类",
                      })),
                    ].map((row, index) => (
                      <tr key={index}>
                        <td>{row.dimension}</td>
                        <td>{row.name}</td>
                        <td>
                          {row.empty
                            ? "无成交记录"
                            : formatMetric(metric, row.value)}
                        </td>
                        {metric.previous != null && (
                          <>
                            <td>{formatMetric(metric, row.previous)}</td>
                            <td>{formatMetric(metric, row.delta ?? null)}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
            {changes.length > 0 && (
              <details className="contribution-data">
                <summary>变化贡献明细（{metric.unit}）</summary>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>维度</th>
                        <th>名称</th>
                        <th>基期</th>
                        <th>本期</th>
                        <th>变化额</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.map((row, index) => (
                        <tr key={index}>
                          <td>
                            {index < metric.contributions.length
                              ? "门店"
                              : "品类"}
                          </td>
                          <td>{row.name}</td>
                          <td>{formatMetric(metric, row.previous)}</td>
                          <td>{formatMetric(metric, row.value)}</td>
                          <td>{formatMetric(metric, row.delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
            {metric.formula && <p className="muted">口径：{metric.formula}</p>}
            {metric.metric === "average" && (
              <p className="muted">
                客单价不可相加；总体客单价按总销售额与总订单数计算。
              </p>
            )}
            {metric.metric === "orders" && (
              <p className="muted">
                同一订单可包含多个品类，品类订单数不可相加作为总订单数。
              </p>
            )}
          </section>
        );
      })}
    </>
  );
}
