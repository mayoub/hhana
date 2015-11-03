import ROOT
from mva.analysis import Analysis
from mva.samples import MC_Ztautau, Pythia_Ztautau, Data, QCD
from mva.samples.others import EWK, Top, MC_Wtaunu
from hhdb.datasets import Database
from mva.variables import VARIABLES, LH_VARIABLES
from mva.categories.lephad import Category_VBF_lh,Category_Boosted_lh,Category_Preselection_lh,Category_wplusjets_CR_lh,Category_Ztautau_CR_lh,Category_Top_CR_lh 
#from mva.categories.lephad import Category_VBF_lh
from mva.plotting import draw_ratio, draw
from tabulate import tabulate

from mva.samples.fakes import OS_SS
from mva.regions import OS_LH, SS_LH

# Instantiate and load the database
DB = Database('datasets_lh')

# Ntuples path
NTUPLE_PATH = '/afs/cern.ch/work/m/mayoub/public/htautau-21-07-2015-working-version-Copy-11-09-2015/eos/atlas/user/m/mayoub/production-v3/'


VARIABLES.update(LH_VARIABLES)


#ztautau = MC_Ztautau(
#    2015, db=DB, 
#    channel='lephad', 
#    ntuple_path=NTUPLE_PATH, 
#    student='lhskim',
#    trigger=False,
#    color='#00A3FF')

ztautau = Pythia_Ztautau(
    2015, db=DB, 
    channel='lephad', 
    ntuple_path=NTUPLE_PATH, 
    student='lhskim',
    trigger=False,
    color='#00A3FF')


top = Top(
    2015, db=DB, 
    channel='lephad', 
    ntuple_path=NTUPLE_PATH, 
    student='lhskim',
    trigger=False,
    color='lightskyblue')

ewk = EWK(
    2015, db=DB, 
    channel='lephad', 
    ntuple_path=NTUPLE_PATH, 
    student='lhskim',
    trigger=False,
    color='#8A0F0F')

data = Data(
    2015,
    ntuple_path=NTUPLE_PATH, 
    student='lhskim',
    channel='lephad',
    label='Data 2015',
    trigger=False)

qcd = Data(
    2015,
    ntuple_path=NTUPLE_PATH, 
    student='lhskim',
    channel='lephad',
    label='QCD',
    color='green',
    trigger=False)

#data_ss_os = OS_SS(data, SS_LH, OS_LH, color='green' , label='QCD')

z_os_ss = OS_SS(ztautau, OS_LH, SS_LH, color='#00A3FF', label='Ztautau')

top_os_ss = OS_SS(top, OS_LH, SS_LH, color='yellow', label='Top')

ewk_os_ss = OS_SS(ewk, OS_LH, SS_LH, color='#8A0F0F', label='EWK')

#data_os_ss = OS_SS(data, OS_LH, SS_LH, label='data')


fields = [
    'jet_0_pt',
    'jet_0_eta',
    'lep_0_pt',
    'tau_0_pt',
    'met_reco_et',
    'pt_ratio_lep_tau',
    'lephad_mmc_mlm_m',
    'lephad_coll_approx_m',
    'lephad_dr',
    'lephad_dphi',
    'lephad_deta',
    'lephad_met_centrality',
    'jets_delta_eta',
    'prod_eta_jets',
    'jets_visible_mass',
    'n_avg_int',
    'lephad_vis_mass',

]

vars = {}
for f in fields:
    if f in VARIABLES.keys():
        vars[f] =  VARIABLES[f]
categories = [Category_Preselection_lh]
#categories = [Category_Preselection_lh, Category_Boosted_lh, Category_VBF_lh, Category_wplusjets_CR_lh, Category_Ztautau_CR_lh, Category_Top_CR_lh ]
headers = [c.name for c in categories]
headers.insert(0, 'sample / category')
#categories = [Category_VBF_lh]
table = []

# for sample in (ztautau, top, ewk, data):
for sample in (ztautau,  data):
    row = [sample.name]
    table.append(row)
    for category in categories:
        events = sample.events(category)
        row.append(
            "{0:.1f} +/- {1:.1f}".format(
                events[1].value, events[1].error))
       
    
print tabulate(table, headers=headers)
print


for cat in categories:
    #a1, b = data.get_field_hist(vars, cat)
    #data.draw_array(a1, cat, 'ALL', field_scale=b)

    a1, b = data.get_field_hist(vars, cat)
    data.draw_array(a1, cat, 'OS_LH', field_scale=b)


    qcd_h, _ = qcd.get_field_hist(vars, cat)
    qcd.draw_array(qcd_h, cat, 'SS_LH', field_scale=b)

    z_h, _ = z_os_ss.get_field_hist(vars, cat)
    z_os_ss.draw_array(z_h, cat, 'ALL', field_scale=b)

    ewk_h, _ = ewk_os_ss.get_field_hist(vars, cat)
    ewk_os_ss.draw_array(ewk_h, cat, 'ALL', field_scale=b)

    t_h, _ = top_os_ss.get_field_hist(vars, cat)
    top_os_ss.draw_array(t_h, cat, 'ALL', field_scale=b)

    
     #z_h, _ = ztautau.get_field_hist(vars, cat)
     #ztautau.draw_array(z_h, cat, 'ALL', field_scale=b)

     #t_h, _ = top.get_field_hist(vars, cat)
     #top.draw_array(t_h, cat, 'ALL', field_scale=b)

     #ewk_h, _ = ewk.get_field_hist(vars, cat)
     #ewk.draw_array(ewk_h, cat, 'ALL', field_scale=b)


    for field in a1:
        # d = a1[field]
        draw(
            vars[field]['root'],
            cat,
           # data=a1[field],
            data=None if a1[field].Integral() == 0 else a1[field],
            model=[ewk_h[field], z_h[field], t_h[field], qcd_h[field]], 
            # model=[t_h[field], ewk_h[field], z_h[field]],
            units=vars[field]['units'] if 'units' in vars[field] else None, 
            logy=False,
            output_name='{0}_{1}.png'.format(field, cat.name))

        #print list(a1[field].y())
        #print a1[field].Integral()
        # HACK: clear the list of canvases
        ROOT.gROOT.GetListOfCanvases().Clear()
