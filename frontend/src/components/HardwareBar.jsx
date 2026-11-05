/**
 * components/HardwareBar.jsx — Top status bar showing GPU/backend info
 */
import React from 'react'
import { Cpu, Zap, HardDrive, RefreshCw } from 'lucide-react'
import { useSystemStore } from '@/stores/systemStore'
import { useShallow } from 'zustand/react/shallow'
import PageGuide from '@/components/common/PageGuide'
import JobStrip from '@/components/common/JobStrip'

export default function HardwareBar() {
  const { backendOnline, hardware, loading, error, init } = useSystemStore(useShallow((s) => ({ backendOnline: s.backendOnline, hardware: s.hardware, loading: s.loading, error: s.error, init: s.init })))

  if (loading) return (
    <div className="h-8 bg-vault-surface border-b border-vault-border flex items-center px-4">
      <span className="text-vault-muted font-mono text-xs animate-pulse">Connecting to backend...</span>
      <span className="ml-auto"><PageGuide /></span>
    </div>
  )

  if (!backendOnline) return (
    <div className="min-h-8 bg-red-950/40 border-b border-red-900/50 flex items-center px-4 gap-2">
      <span className="text-red-400 font-mono text-xs">⚠ Backend offline{error ? ` — ${error}` : ' — double-click backend_start.bat in the VoiceVault folder'}</span>
      <button onClick={() => init()} className="ml-auto flex items-center gap-1 text-red-300 hover:text-white font-mono text-xs">
        <RefreshCw size={11} /> Retry
      </button>
    </div>
  )

  const gpu = hardware?.gpus?.[0]
  const vramUsed = gpu ? (gpu.vram_total_mb - gpu.vram_free_mb) : 0
  const vramPct  = gpu ? Math.round((vramUsed / gpu.vram_total_mb) * 100) : 0

  return (
    <div className="h-8 bg-vault-surface border-b border-vault-border flex items-center px-4 gap-6">
      <div className="flex items-center gap-1.5">
        <Zap size={11} className="text-vault-accent" />
        <span className="font-mono text-[10px] text-vault-muted uppercase tracking-wider">
          {hardware?.acceleration ?? 'cpu'}
        </span>
      </div>
      {gpu && (
        <>
          <div className="flex items-center gap-1.5">
            <Cpu size={11} className="text-vault-muted" />
            <span className="font-mono text-[10px] text-vault-text truncate max-w-[160px]">{gpu.name}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <HardDrive size={11} className="text-vault-muted" />
            <span className="font-mono text-[10px] text-vault-muted">
              {Math.round(vramUsed)}MB / {Math.round(gpu.vram_total_mb)}MB
            </span>
            <div className="w-20 h-1 bg-vault-border rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${vramPct}%`,
                  background: vramPct > 85 ? '#ef4444' : vramPct > 65 ? '#f59e0b' : 'rgb(var(--vv-accent))'
                }}
              />
            </div>
          </div>
        </>
      )}
      <div className="ml-auto min-w-0 flex justify-end"><JobStrip /></div>
      <div className="shrink-0 font-mono text-[10px] text-vault-muted">
        ffmpeg: <span className="text-vault-text">{hardware?.ffmpeg_hw_encoder ?? 'none'}</span>
      </div>
      <PageGuide />
    </div>
  )
}
