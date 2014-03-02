from .mva import *
from .cuts import *
from .mva_cuts_overlap import *

CATEGORIES = {
    'cuts_presel': [
        Category_Cuts_Preselection,
        ],
    'cuts' : [
        Category_Cuts_VBF_LowDR,
        Category_Cuts_VBF_HighDR_Tight,
        Category_Cuts_VBF_HighDR_Loose,
        Category_Cuts_Boosted_Tight,
        Category_Cuts_Boosted_Loose,
        ],
    'cuts_merged' : [
    Category_Cuts_VBF,
    Category_Cuts_Boosted,
    ],
    'presel': [
        Category_Preselection,
        ],
    'presel_deta_controls': [
        Category_Preselection_DEta_Control,
        ],
    'mva': [
        Category_VBF,
        Category_Boosted,
    ],
    'mva_all': [
        Category_VBF,
        Category_Boosted,
        Category_Rest,
    ],
    'mva_deta_controls': [
        Category_VBF_DEta_Control,
        Category_Boosted_DEta_Control,
    ],
    'mva_workspace_controls': [
        Category_Rest,
    ],
    'overlap': [
    Category_Cut_VBF_MVA_VBF,
    Category_Cut_Boosted_MVA_Boosted,
    Category_Cut_Presel_MVA_Presel,
    ],
    'overlap_details': [
    Category_Cut_VBF_MVA_VBF,
    Category_Cut_VBF_MVA_Boosted,
    Category_Cut_VBF_MVA_Presel,
    Category_Cut_Boosted_MVA_VBF,
    Category_Cut_Boosted_MVA_Boosted,
    Category_Cut_Boosted_MVA_Presel,
    Category_Cut_Presel_MVA_VBF,
    Category_Cut_Presel_MVA_Boosted,
    Category_Cut_Presel_MVA_Presel,
    Category_Cut_VBF_Not_MVA_VBF,
    Category_Cut_VBF_Not_MVA_Boosted,
    Category_Cut_VBF_Not_MVA_Presel,
    Category_Cut_Boosted_Not_MVA_VBF,
    Category_Cut_Boosted_Not_MVA_Boosted,
    Category_Cut_Boosted_Not_MVA_Presel,
    Category_Cut_Presel_Not_MVA_VBF,
    Category_Cut_Presel_Not_MVA_Boosted,
    Category_Cut_Presel_Not_MVA_Presel,
    Category_MVA_Presel_Not_Cut_VBF,
    Category_MVA_Presel_Not_Cut_Boosted,
    Category_MVA_Presel_Not_Cut_Presel,
    Category_MVA_VBF_Not_Cut_VBF,
    Category_MVA_VBF_Not_Cut_Boosted,
    Category_MVA_VBF_Not_Cut_Presel,
    Category_MVA_Boosted_Not_Cut_VBF,
    Category_MVA_Boosted_Not_Cut_Boosted,
    Category_MVA_Boosted_Not_Cut_Presel,
    ]




}