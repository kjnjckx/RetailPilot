import React, { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { Obj, when, api } from "../lib";
import { Empty } from "../components/ui";

export default function Audit({ notify }: { notify: (s: string) => void }) {
  const [events, setEvents] = useState<Obj[]>([]),
    [search, setSearch] = useState("");
  useEffect(() => {
    api("/audit")
      .then(setEvents)
      .catch((e) => notify(e.message));
  }, []);
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>操作审计</h2>
          <p>管理员可查看全部记录，其他账号仅显示自身操作 · 最近 200 条</p>
        </div>
        <div className="search-box">
          <Search size={16} />
          <input
            placeholder="搜索操作者、动作或对象"
            aria-label="搜索日志"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>操作时间</th>
              <th>操作者</th>
              <th>动作</th>
              <th>业务对象</th>
            </tr>
          </thead>
          <tbody>
            {events
              .filter((e) => (e.actor + e.action + e.target).includes(search))
              .map((e) => (
                <tr key={e.id}>
                  <td>{when(e.at)}</td>
                  <td>
                    <span className="audit-person">
                      <span className="avatar small">{e.actor[0]}</span>
                      {e.actor}
                    </span>
                  </td>
                  <td>{e.action}</td>
                  <td className="muted">{e.target}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!events.length && (
        <Empty
          text="暂无操作记录"
          hint="登录、导入、分析和审核等动作将自动留痕。"
        />
      )}
    </section>
  );
}
