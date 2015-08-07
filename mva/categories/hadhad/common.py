from rootpy.tree import Cut
from math import pi

from ..base import Category
from ... import MMC_MASS
# All basic cut definitions are here

TRIG1 = Cut('HLT_tau35_medium1_tracktwo_tau25_loose1_tracktwo_L1TAU20IM_2TAU12IM == 1')
TRIG2 = Cut('HLT_tau35_loose1_tracktwo_tau25_loose1_tracktwo_L1TAU20IM_2TAU12IM == 1')
TRIGGER = TRIG1 | TRIG2

TAU1_MEDIUM = Cut('tau_0_jet_bdt_medium==1')
TAU2_MEDIUM = Cut('tau_1_jet_bdt_medium==1')
TAU1_TIGHT = Cut('tau_0_jet_bdt_tight==1')
TAU2_TIGHT = Cut('tau_1_jet_bdt_tight==1')

ID_MEDIUM = TAU1_MEDIUM & TAU2_MEDIUM
ID_TIGHT = TAU1_TIGHT & TAU2_TIGHT
ID_MEDIUM_TIGHT = (TAU1_MEDIUM & TAU2_TIGHT) | (TAU1_TIGHT & TAU2_MEDIUM)
# ID cuts for control region where both taus are medium but not tight
ID_MEDIUM_NOT_TIGHT = (TAU1_MEDIUM & -TAU1_TIGHT) & (TAU2_MEDIUM & -TAU2_TIGHT)

TAU_SAME_VERTEX = Cut('tau_same_vertex')

LEAD_TAU_40 = Cut('tau_0_pt > 40')
SUBLEAD_TAU_30 = Cut('tau_1_pt > 30')

LEAD_JET_50 = Cut('jet_0_pt > 50')
SUBLEAD_JET_30 = Cut('jet_1_pt > 30')
AT_LEAST_1JET = Cut('jet_0_pt > 30')

CUTS_2J = LEAD_JET_50 & SUBLEAD_JET_30
CUTS_1J = LEAD_JET_50 & (- SUBLEAD_JET_30)
CUTS_0J = (- LEAD_JET_50)

MET = Cut('met_et > 20')
DR_TAUS = Cut('0.8 < tau_tau_dr < 2.4')
DETA_TAUS = Cut('tau_tau_deta < 1.5')
DETA_TAUS_CR = Cut('dEta_tau1_tau2 > 1.5')
RESONANCE_PT = Cut('tau_tau_vect_sum_pt > 100')

# use .format() to set centality value
MET_CENTRALITY = 'tau_tau_met_bisect==1 || (tau_tau_met_min_dphi < {0})'

# common preselection cuts
PRESELECTION = (
    # TRIGGER
    LEAD_TAU_40 & SUBLEAD_TAU_30
    & ID_MEDIUM
    & MET
    & Cut('%s > 0' % MMC_MASS)
    & DR_TAUS
    # & TAU_SAME_VERTEX
    )

# VBF category cuts
CUTS_VBF = (
    CUTS_2J
    & DETA_TAUS
    )

CUTS_VBF_CR = (
    CUTS_2J
    & DETA_TAUS_CR
    )

# Boosted category cuts
CUTS_BOOSTED = (
    RESONANCE_PT
    & DETA_TAUS
    )

CUTS_BOOSTED_CR = (
    RESONANCE_PT
    & DETA_TAUS_CR
    )


class Category_Preselection_NO_MET_CENTRALITY(Category):
    name = 'preselection'
    label = '#tau_{had}#tau_{had} Preselection'
    common_cuts = PRESELECTION


class Category_Preselection(Category):
    name = 'preselection'
    label = '#tau_{had}#tau_{had} Preselection'
    common_cuts = (
        PRESELECTION
        # & Cut(MET_CENTRALITY.format(pi / 4))
        )


class Category_Preselection_DEta_Control(Category_Preselection):
    is_control = True
    name = 'preselection_deta_control'


class Category_1J_Inclusive(Category_Preselection):
    name = '1j_inclusive'
    label = '#tau_{had}#tau_{had} Inclusive 1-Jet'
    common_cuts = Category_Preselection.common_cuts
    cuts = AT_LEAST_1JET
    norm_category = Category_Preselection
