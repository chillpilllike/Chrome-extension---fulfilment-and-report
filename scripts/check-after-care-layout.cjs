// Offline layout and action-contract checks. No API calls or emails.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const cp = require('node:child_process')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const appPath = 'frontend/src/App.tsx'
const app = fs.readFileSync(appPath, 'utf8')
const before = cp.execFileSync('git', ['show', 'a17c875:frontend/src/App.tsx'], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 })
function handlers(source) {
  const tree = ts.createSourceFile(appPath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const page = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'AfterOrderCarePage')
  const result = {}
  page.body.statements.forEach(node => {
    if (ts.isVariableStatement(node)) node.declarationList.declarations.forEach(decl => {
      if (ts.isIdentifier(decl.name) && decl.initializer) result[decl.name.text] = decl.initializer.getText(tree)
    })
  })
  return result
}
const oldHandlers = handlers(before), newHandlers = handlers(app)
for (const name of ['approveUnavailableNotice', 'confirmDecision', 'sendTestEmail', 'sendAllTestEmails', 'updateTestMode', 'createTestLink', 'addRecommendedProduct', 'openActivity']) {
  assert.ok(oldHandlers[name], name)
  assert.equal(newHandlers[name], oldHandlers[name], `${name} must retain its existing action contract`)
}
const source = fs.readFileSync('frontend/src/components/AfterCareWorkspace.tsx', 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText
const moduleObject = { exports: {} }
vm.runInNewContext(compiled, { exports: moduleObject.exports, require: name => {
  if (name.endsWith('.css')) return {}
  if (name.includes('/ui/button')) return { Button: props => React.createElement('button', props) }
  if (name.includes('/ui/input')) return { Input: props => React.createElement('input', props) }
  return require(name)
}})
let selected, searched = false
const props = { status: 'needs_confirmation', summary: { needs_confirmation: 2, needs_attention: 3, approved: 4, resolved: 5 }, loading: false, total: 2, query: 'NC123', onQuery() {}, onSearch() { searched = true }, onQueue(value) { selected = value }, onRefresh() {}, testMode: true, automationEnabled: false, cutoffDate: '2026-08-01', tools: 'Test controls', children: 'Case details' }
const tree = moduleObject.exports.AfterCareWorkspace(props)
const html = renderToStaticMarkup(tree)
assert.match(html, /epost-layout/)
assert.match(html, /Test mode · no live actions/)
assert.match(html, /aria-pressed="true"[^>]*><span>Confirm decisions/)
assert.match(html, /All open cases<\/span><strong>5/)
assert.match(html, /All cases<\/span><strong>14/)
assert.match(html, /Email log &amp; retries/)
function visit(node) {
  if (!node || typeof node !== 'object') return
  if (node.type === 'form') node.props.onSubmit({ preventDefault() {} })
  if (node.type === 'button' && node.props.className?.includes('epost-queue')) node.props.onClick()
  React.Children.forEach(node.props?.children, visit)
}
visit(tree)
assert.equal(searched, true)
assert.equal(selected, 'all')
assert.match(app, /page: String\(page\)/)
assert.match(app, /requestId !== requestRef.current/)
console.log('PASS: queue rendering, search and queue callbacks, pagination wiring, and eight existing action handlers preserved')
