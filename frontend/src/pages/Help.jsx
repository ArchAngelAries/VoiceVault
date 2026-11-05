/**
 * pages/Help.jsx — Every page guide in one place, how to connect an AI agent (MCP),
 * API keys, model licences and the terms of use. ?section=<key> opens a section directly.
 */
import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CircleHelp, Bot, Copy, Check, KeyRound, Scale, ScrollText } from 'lucide-react'
import clsx from 'clsx'
import { PAGE_GUIDES } from '@/data/pageGuides'
import { GuideBody } from '@/components/common/PageGuide'
import { ApiKeysHelp, ModelLicensesHelp, TermsHelp } from '@/components/common/HelpReference'
import { ISSUES_URL, PROJECT_URL } from '@/data/support'

const EXTRA_SECTIONS = [
  ['agents', 'AI agents', Bot],
  ['api-keys', 'API keys', KeyRound],
  ['licences', 'Models & licences', Scale],
  ['terms', 'Terms of use', ScrollText],
]

const MCP_CONFIG = `{
  "mcpServers": {
    "voicevault": {
      "command": "<VoiceVault folder>/venv/Scripts/python.exe",
      "args": ["<VoiceVault folder>/backend/mcp_server.py"]
    }
  }
}`

export default function Help() {
  const keys = Object.keys(PAGE_GUIDES)
  const [params] = useSearchParams()
  const known = (k) => (k && (PAGE_GUIDES[k] || EXTRA_SECTIONS.some(([id]) => id === k)) ? k : null)
  const [active, setActive] = useState(() => known(params.get('section')) || 'agents')
  useEffect(() => { const k = known(params.get('section')); if (k) setActive(k) }, [params])
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(MCP_CONFIG); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* no clipboard */ }
  }
  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 gap-6 animate-fade-in">
      <div className="flex items-center gap-3">
        <CircleHelp size={22} className="text-vault-accent" />
        <h1 className="font-display text-2xl text-vault-text">Help</h1>
      </div>
      <div className="flex flex-col md:flex-row gap-6">
        <nav className="flex md:flex-col flex-wrap gap-1 md:w-48 shrink-0">
          {keys.map(k => (
            <button key={k} onClick={() => setActive(k)}
              className={clsx('text-left font-body text-sm px-3 py-1.5 rounded-lg', active === k ? 'bg-vault-elevated text-vault-glow' : 'text-vault-muted hover:text-vault-text')}>
              {PAGE_GUIDES[k].title}</button>
          ))}
          {EXTRA_SECTIONS.map(([id, label, Icon]) => (
            <button key={id} onClick={() => setActive(id)}
              className={clsx('text-left font-body text-sm px-3 py-1.5 rounded-lg inline-flex items-center gap-1.5', active === id ? 'bg-vault-elevated text-vault-glow' : 'text-vault-muted hover:text-vault-text')}>
              <Icon size={14} /> {label}</button>
          ))}
          <div className="md:mt-3 md:pt-3 md:border-t border-vault-border/60 flex md:flex-col gap-1">
            <a href={ISSUES_URL} target="_blank" rel="noopener noreferrer" className="font-body text-xs px-3 py-1 text-vault-muted hover:text-vault-text">Report a problem ↗</a>
            <a href={PROJECT_URL} target="_blank" rel="noopener noreferrer" className="font-body text-xs px-3 py-1 text-vault-muted hover:text-vault-text">Source code ↗</a>
          </div>
        </nav>
        <div className="vault-card p-5 flex-1 max-w-3xl">
          {active === 'api-keys' ? <ApiKeysHelp />
          : active === 'licences' ? <ModelLicensesHelp />
          : active === 'terms' ? <TermsHelp />
          : active === 'agents' ? (
            <div className="flex flex-col gap-4 font-body text-sm text-vault-text" data-testid="help-agents">
              <h2 className="font-display text-lg">Let an AI agent use VoiceVault</h2>
              <p className="text-vault-muted">A local model in LM Studio, Claude Desktop or any MCP client can speak lines, reshape voices, analyse and compose music, and label your Library. It talks to VoiceVault while VoiceVault is running.</p>
              <ol className="list-decimal pl-5 flex flex-col gap-1.5">
                <li>Keep VoiceVault running.</li>
                <li>In LM Studio, open Program → Edit mcp.json (in Claude Desktop, its config file) and add the block below.</li>
                <li>Replace <code className="font-mono text-xs">&lt;VoiceVault folder&gt;</code> with the folder VoiceVault is installed in.</li>
                <li>Pick a model that supports tools, and ask it, for example, “say ‘welcome aboard’ in the Professor Quill voice”.</li>
              </ol>
              <div className="relative">
                <pre className="rounded-lg border border-vault-border bg-vault-elevated p-3 font-mono text-xs overflow-x-auto">{MCP_CONFIG}</pre>
                <button onClick={copy} className="absolute top-2 right-2 vault-btn-ghost px-2 py-1 text-[11px] inline-flex items-center gap-1">
                  {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? 'Copied' : 'Copy'}</button>
              </div>
              <h3 className="vault-label">Built to be safe</h3>
              <ul className="list-disc pl-5 flex flex-col gap-1.5 text-vault-text/90">
                <li>Agents cannot delete, move or overwrite anything. New work is always added as a new file, take or preview.</li>
                <li>Names are matched carefully. When one is unclear, the agent gets the closest names and asks again instead of guessing.</li>
                <li>No file paths from your PC are shared, only names and listening links.</li>
                <li>Fetching from links and deleting stay in the app, under your control.</li>
              </ul>
              <p className="text-xs text-vault-muted">Full reference: docs/AGENT_MANUAL.md.</p>
            </div>
          ) : <><h2 className="font-display text-lg text-vault-text mb-3">{PAGE_GUIDES[active].title}</h2><GuideBody guide={PAGE_GUIDES[active]} /></>}
        </div>
      </div>
    </div>
  )
}
