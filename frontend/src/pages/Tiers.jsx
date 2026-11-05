/**
 * pages/Tiers.jsx — Tier lists. /tiers lists the boards; /tiers/:tierId opens one.
 */
import React from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import TierBoardPage from '@/components/tiers/TierBoardPage'
import TierListPage from '@/components/tiers/TierListPage'

export default function Tiers() {
  const { tierId } = useParams()
  const navigate = useNavigate()
  if (tierId) {
    return (
      <div className="flex-1 flex flex-col p-6 md:p-8 gap-6 animate-fade-in">
        <TierBoardPage key={tierId} tierId={Number(tierId)} onBack={() => navigate('/tiers')} />
      </div>
    )
  }
  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 gap-6 animate-fade-in">
      <TierListPage onOpen={(id) => navigate(`/tiers/${id}`)} />
    </div>
  )
}
