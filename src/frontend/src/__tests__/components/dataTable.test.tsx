import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

import {DataTable, DataTableColumn} from "@/components/common/DataTable";

// The table primitive the admin tabs render through. These pin the markup
// contract the migrated tabs rely on: one <th> per column, one <tr>/<td> per
// row and column, class names passed through verbatim, an empty state only
// when there are no rows, a colgroup only when a column asks for one, and a
// sort that orders rows without touching the caller's array.

type Row = {id: number; name: string; size: number};

const rows: Row[] = [
  {id: 1, name: "beam.step", size: 300},
  {id: 2, name: "arch.ifc", size: 100},
  {id: 3, name: "crane.glb", size: 200},
];

const columns: DataTableColumn<Row>[] = [
  {key: "name", header: "Name", cell: (r) => r.name, title: (r) => `key:${r.name}`, sortValue: (r) => r.name},
  {key: "size", header: "Size", cell: (r) => String(r.size), cellClassName: "text-right", sortValue: (r) => r.size},
];

const render = (el: React.ReactElement) => renderToStaticMarkup(el);
const count = (html: string, re: RegExp) => (html.match(re) ?? []).length;

test("renders one header per column and one cell per row and column", () => {
  const html = render(
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      className="w-full text-sm"
      headerCellClassName="px-3 py-2"
      cellClassName="px-3 py-1"
      rowClassName="border-t"
    />,
  );
  assert.equal(count(html, /<th\b/g), 2);
  assert.equal(count(html, /<tr\b/g), 4); // header row + 3 body rows
  assert.equal(count(html, /<td\b/g), 6);
  assert.ok(html.includes('<table class="w-full text-sm">'));
  assert.ok(html.includes('<th class="px-3 py-2">Name</th>'));
  // Column-level cell class replaces the table default; the title comes from the column.
  assert.ok(html.includes('<td class="px-3 py-1" title="key:beam.step">beam.step</td>'));
  assert.ok(html.includes('<td class="text-right">300</td>'));
  assert.equal(count(html, /<tr class="border-t">/g), 3);
  // Wrapped in the overflow-x container by default.
  assert.ok(html.startsWith('<div class="overflow-x-auto"><table'));
});

test("wrap=false renders the bare table; stickyHeader adds sticky top-0 to thead", () => {
  const html = render(
    <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} wrap={false} stickyHeader theadClassName="bg-gray-800"/>,
  );
  assert.ok(html.startsWith("<table"));
  assert.ok(html.includes('<thead class="sticky top-0 bg-gray-800">'));
});

test("empty state renders only when there are no rows", () => {
  const empty = <div className="empty">No files in this scope.</div>;
  const withRows = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} emptyState={empty}/>);
  assert.ok(!withRows.includes("No files in this scope."));
  const without = render(<DataTable columns={columns} rows={[]} rowKey={(r) => r.id} emptyState={empty}/>);
  assert.ok(without.includes('<div class="empty">No files in this scope.</div>'));
  // The table itself still renders (header stays visible), with an empty body.
  assert.ok(without.includes("<tbody></tbody>"));
});

test("colgroup appears only when a column declares a col", () => {
  const plain = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id}/>);
  assert.ok(!plain.includes("<colgroup>"));
  const withCols = render(
    <DataTable
      columns={[{...columns[0], col: {className: "min-w-[5rem]"}}, columns[1]]}
      rows={rows}
      rowKey={(r) => r.id}
    />,
  );
  assert.ok(withCols.includes('<colgroup><col class="min-w-[5rem]"/><col/></colgroup>'));
});

test("defaultSort orders rows by the column's sortValue and marks the header", () => {
  const html = render(
    <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} defaultSort={{key: "size", desc: true}}/>,
  );
  const order = [...html.matchAll(/<td[^>]*>([a-z]+\.[a-z]+)<\/td>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["beam.step", "crane.glb", "arch.ifc"]);
  assert.ok(html.includes('aria-sort="descending"'));
  assert.ok(html.includes('<span class="text-blue-400"> ▾</span>'));
  // The caller's array is left alone.
  assert.deepEqual(rows.map((r) => r.id), [1, 2, 3]);
});

test("string sort is locale-ascending by default", () => {
  const html = render(
    <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} sort={{key: "name", desc: false}}/>,
  );
  const order = [...html.matchAll(/<td[^>]*>([a-z]+\.[a-z]+)<\/td>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["arch.ifc", "beam.step", "crane.glb"]);
});

test("renderRow replaces the default row; renderAfterRow appends expansion rows", () => {
  const custom = render(
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      renderRow={(r) => <tr className="custom"><td colSpan={2}>{r.name}</td></tr>}
    />,
  );
  assert.equal(count(custom, /<tr class="custom">/g), 3);
  assert.equal(count(custom, /<td colSpan="2">|<td colspan="2">/gi), 3);
  const expanded = render(
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.id}
      renderAfterRow={(r) => r.id === 2 && <tr className="expansion"><td colSpan={2}>details</td></tr>}
    />,
  );
  assert.equal(count(expanded, /<tr class="expansion">/g), 1);
  assert.equal(count(expanded, /<tr\b/g), 5);
});
