// Render the real workspace with lightweight UI primitives; exercise selection callbacks.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const filename = path.resolve(__dirname, '../src/components/EpostWorkspace.tsx')
let checkboxes = []
const primitive = tag => ({ children }) => React.createElement(tag, null, children)
const compiled = new Module(filename, module)
compiled.paths = module.paths
compiled.require = name => {
  if (name.endsWith('.css')) return {}
  if (name.endsWith('/checkbox')) return { Checkbox: props => { checkboxes.push(props); return React.createElement('input', { type: 'checkbox', checked: props.checked, readOnly: true }) } }
  if (name.startsWith('@/components/ui/')) return new Proxy({}, { get: (_, key) => key === 'Dialog' ? () => null : primitive(key.startsWith('Table') ? ({ Table: 'table', TableHeader: 'thead', TableBody: 'tbody', TableRow: 'tr', TableHead: 'th', TableCell: 'td' }[key] || 'div') : 'div') })
  return require(name)
}
compiled._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename)
const { EpostWorkspace } = compiled.exports
let state = { selected: [], selectAll: false }
const rows = [1, 2, 3].map(id => ({ id, tracking_code: `EPG${id}`, odoo_order_name: `NC${id}` }))
function render(overrides = {}) {
  checkboxes = []
  return renderToStaticMarkup(React.createElement(EpostWorkspace, {
    rows, total: 30, page: 1, pageSize: 3, storeId: '1', queue: 'attention', query: '', staleDays: '10', loading: false,
    ...state, onSelected: ids => { state.selected = ids }, onSelectAll: value => { state.selectAll = value },
    exports: React.createElement('button', null, 'Download CSV'), ...overrides,
  }))
}
let html = render()
assert.ok(html.indexOf('Download CSV') < html.indexOf('<table'))
assert.ok(!html.includes('<details'))
checkboxes[0].onCheckedChange(true)
assert.deepEqual(state.selected, [1, 2, 3])
render()
assert.ok(checkboxes.every(box => box.checked))
checkboxes[2].onCheckedChange(false)
assert.deepEqual(state.selected, [2, 3])
state = { selected: [9], selectAll: false }
render()
checkboxes[0].onCheckedChange(true)
assert.deepEqual(state.selected, [9, 1, 2, 3])
render()
checkboxes[1].onCheckedChange(false)
assert.deepEqual(state.selected, [9])
state = { selected: [], selectAll: true }
render()
assert.ok(checkboxes.every(box => box.checked))
checkboxes[2].onCheckedChange(false)
assert.equal(state.selectAll, false)
assert.deepEqual(state.selected, [2, 3])
state = { selected: [], selectAll: true }
render()
checkboxes[0].onCheckedChange(false)
assert.deepEqual(state, { selected: [], selectAll: false })
render({ loading: true })
assert.ok(checkboxes.every(box => box.disabled))
render({ rows: [], total: 0 })
assert.ok(checkboxes.every(box => box.disabled && !box.checked))
const css = fs.readFileSync(path.resolve(__dirname, '../src/components/epost-workspace.css'), 'utf8')
assert.match(css, /\.epost-workspace \.epost-main > \.tabler-sticky-table-wrap\s*\{[^}]*max-height: none/)
console.log('ePost workspace: export placement, page/cross-page/all-matching selection, loading, empty state and scoped height checks passed.')
