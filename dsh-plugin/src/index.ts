/**
 * DeepSeek Harness plugin: Cadfree CAD + DFM + MATLAB tools.
 *
 * Mount beside dsh rather than forking the harness. Everything is a plugin —
 * this one shells into the Cadfree Python runtime so geometry, shop checks,
 * and Octave stay in one place.
 *
 *   dsh --patch ./dsh-plugin/cordis.patch.yml
 *
 * CADFREE_PROJECT_id must be set (or pass project_id in the tool args).
 */
import { spawn } from 'node:child_process'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'cadfree-tools'
export const inject = ['tools']

function runCli(projectId: string, tool: string, args: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const child = spawn('python', ['-m', 'cadfree.cli', 'tool', projectId, tool, JSON.stringify(args)], {
      cwd: process.env.CADFREE_ROOT || process.cwd(),
    })
    let out = ''
    let err = ''
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    child.on('close', (code) => {
      if (code !== 0) reject(new Error(err || out || `cadfree cli exited ${code}`))
      else {
        try { resolve(JSON.parse(out)) } catch { resolve(out) }
      }
    })
  })
}

export default function apply(ctx: Context) {
  const projectId = () => process.env.CADFREE_PROJECT_ID || ''

  ctx.tools.register(defineTool({
    name: 'cadfree_get_workshop',
    description: 'List Cadfree workshop machines, materials, and solver availability.',
    parameters: {},
    async execute() {
      return runCli(projectId(), 'get_workshop', {})
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_write_cadquery',
    description: 'Replace the CadQuery script for the active Cadfree project.',
    parameters: { source: { type: 'string', required: true } },
    async execute(args: { source: string }) {
      return runCli(projectId(), 'write_cadquery', { source: args.source })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_build_model',
    description: 'Build CadQuery and return mass/bbox metrics.',
    parameters: {},
    async execute() {
      return runCli(projectId(), 'build_model', {})
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_check_feasibility',
    description: 'DFM + mass + first-order strength against the user shop.',
    parameters: {},
    async execute() {
      return runCli(projectId(), 'check_feasibility', {})
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_run_matlab',
    description: 'Run MATLAB or Octave in Cadfree agent mode.',
    parameters: { code: { type: 'string', required: true } },
    async execute(args: { code: string }) {
      return runCli(projectId(), 'run_matlab', { code: args.code })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_run_simulation',
    description: 'Run first-order / MATLAB / FEA rungs for the current part.',
    parameters: { prefer: { type: 'string' } },
    async execute(args: { prefer?: string }) {
      return runCli(projectId(), 'run_simulation', { prefer: args.prefer || 'auto' })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_search_standards',
    description: 'Search ISO/ASTM/ASME/DIN/SAE/MIL-STD and manufacturer datasheets. Cite URLs; do not invent paywalled clauses.',
    parameters: {
      query: { type: 'string', required: true },
      intent: { type: 'string' },
    },
    async execute(args: { query: string, intent?: string }) {
      return runCli(projectId(), 'search_standards', { query: args.query, intent: args.intent || 'standards' })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_read_url',
    description: 'Fetch a public standards or datasheet page. Private IPs are refused.',
    parameters: { url: { type: 'string', required: true } },
    async execute(args: { url: string }) {
      return runCli(projectId(), 'read_url', { url: args.url })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'cadfree_ask_survey',
    description: 'Create a structured survey for the Cadfree studio. The Python app waits; this CLI call only creates the form.',
    parameters: { title: { type: 'string' }, questions: { type: 'array' } },
    async execute(args: { title?: string, questions: unknown }) {
      return runCli(projectId(), 'ask_survey', { title: args.title || '', questions: args.questions })
    },
  }))
}
