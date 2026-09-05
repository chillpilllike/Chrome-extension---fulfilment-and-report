// Usage: NODE_PATH=frontend/node_modules node scripts/check-team-ui-contract.cjs before.tsx frontend/src/App.tsx
// Compare a UI-only edit against its pre-edit source without calling any live API.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const ts = require('typescript')
const [beforePath, afterPath] = process.argv.slice(2)
assert(beforePath && afterPath, 'Provide the before and after TSX files')
const printer = ts.createPrinter({ removeComments: true })
function inventory(path) {
  const file = ts.createSourceFile(path, fs.readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  assert.equal(file.parseDiagnostics.length, 0, `TSX parse failed: ${path}`)
  const attributes = new Map(), requests = new Map(), effects = new Map()
  const add = (map, node) => {
    const key = printer.printNode(ts.EmitHint.Unspecified, node, file).replace(/\s+/g, ' ').trim()
    map.set(key, (map.get(key) || 0) + 1)
  }
  function visit(node) {
    if (ts.isJsxAttribute(node) && /^(onClick|onChange|onCheckedChange|onSubmit|onOpenChange|disabled|checked)$/.test(node.name.text)) add(attributes, node)
    if (ts.isCallExpression(node)) {
      const name = node.expression.getText(file)
      if (name === 'api' || name === 'fetch' || name === 'window.confirm') add(requests, node)
      if (name === 'useEffect') add(effects, node)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return { attributes, requests, effects }
}
const before = inventory(beforePath), after = inventory(afterPath)
for (const group of ['attributes', 'requests', 'effects']) {
  for (const [key, count] of before[group]) {
    assert((after[group].get(key) || 0) >= count, `Existing ${group} changed or removed: ${key}`)
  }
  if (group !== 'attributes') assert.deepEqual(after[group], before[group], `${group} must remain exactly unchanged`)
  console.log(`PASS: ${[...before[group].values()].reduce((a, b) => a + b, 0)} existing ${group} preserved`)
}
