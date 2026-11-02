'use strict'

function makeWalker(asyncMode) {
  return function walk(ast, { enter, leave }) {
    const visit = asyncMode ? asyncVisit : syncVisit

    function context(state) {
      return {
        skip() { state.skip = true },
        remove() { state.remove = true },
        replace(value) { state.replacement = value },
      }
    }

    function applyState(state, parent, key, index) {
      if (state.replacement !== undefined) {
        if (index === null) parent[key] = state.replacement
        else parent[key][index] = state.replacement
        return state.replacement
      }
      if (state.remove && parent) {
        if (index === null) delete parent[key]
        else parent[key].splice(index, 1)
        return null
      }
      return state.node
    }

    function syncVisit(node, parent = null, key = null, index = null) {
      if (!node || typeof node.type !== 'string') return
      const state = { node, skip: false, remove: false, replacement: undefined }
      if (enter) enter.call(context(state), node, parent, key, index)
      node = applyState(state, parent, key, index)
      if (!node) return
      if (!state.skip) {
        for (const childKey of Object.keys(node)) {
          const value = node[childKey]
          if (Array.isArray(value)) {
            for (let childIndex = 0; childIndex < value.length; childIndex += 1) {
              const lengthBefore = value.length
              syncVisit(value[childIndex], node, childKey, childIndex)
              if (value.length < lengthBefore) childIndex -= 1
            }
          } else if (value && typeof value.type === 'string') {
            syncVisit(value, node, childKey, null)
          }
        }
      }
      if (leave) leave.call(context(state), node, parent, key, index)
      applyState(state, parent, key, index)
    }

    async function asyncVisit(node, parent = null, key = null, index = null) {
      if (!node || typeof node.type !== 'string') return
      const state = { node, skip: false, remove: false, replacement: undefined }
      if (enter) await enter.call(context(state), node, parent, key, index)
      node = applyState(state, parent, key, index)
      if (!node) return
      if (!state.skip) {
        for (const childKey of Object.keys(node)) {
          const value = node[childKey]
          if (Array.isArray(value)) {
            for (let childIndex = 0; childIndex < value.length; childIndex += 1) {
              const lengthBefore = value.length
              await asyncVisit(value[childIndex], node, childKey, childIndex)
              if (value.length < lengthBefore) childIndex -= 1
            }
          } else if (value && typeof value.type === 'string') {
            await asyncVisit(value, node, childKey, null)
          }
        }
      }
      if (leave) await leave.call(context(state), node, parent, key, index)
      applyState(state, parent, key, index)
    }

    return visit(ast)
  }
}

exports.walk = makeWalker(false)
exports.asyncWalk = makeWalker(true)
