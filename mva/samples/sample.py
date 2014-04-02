# std lib imports
import os
import sys
from operator import add, itemgetter

# numpy imports
import numpy as np
from numpy.lib import recfunctions

# rootpy imports
import ROOT
from rootpy.plotting import Hist, Hist2D, Canvas, HistStack
from rootpy.plotting.hist import _HistBase
from rootpy.tree import Tree, Cut
from rootpy.stats import histfactory
from rootpy import asrootpy

# root_numpy imports
from root_numpy import rec2array, stack

# higgstautau imports
from higgstautau import samples as samples_db

# local imports
from . import log; log = log[__name__]
from .. import variables
from .. import DEFAULT_STUDENT, ETC_DIR
from ..utils import print_hist, ravel_hist, uniform_hist
from ..classify import histogram_scores, Classifier
from ..regions import REGIONS
from ..systematics import (
    get_systematics, SYSTEMATICS_BY_WEIGHT,
    iter_systematics, systematic_name)
from ..lumi import LUMI, get_lumi_uncert
from .db import DB, TEMPFILE, get_file
from ..cachedtable import CachedTable


def get_workspace_np_name(sample, syst, year):
    # https://twiki.cern.ch/twiki/bin/viewauth/AtlasProtected/HiggsPropertiesNuisanceParameterNames
    npname = 'ATLAS_{0}_{1:d}'.format(syst, year)
    npname = npname.replace('JES_Detector_2012', 'JES_2012_Detector1')
    npname = npname.replace('JES_EtaMethod_2012', 'JES_2012_Eta_StatMethod')
    npname = npname.replace('JES_EtaModelling_2012', 'JES_Eta_Modelling')
    npname = npname.replace('JES_FlavComp_TAU_G_2012', 'JES_FlavComp_TAU_G')
    npname = npname.replace('JES_FlavComp_TAU_Q_2012', 'JES_FlavComp_TAU_Q')
    npname = npname.replace('JES_FlavResp_2012', 'JES_FlavResp')
    npname = npname.replace('JES_Modelling_2012', 'JES_2012_Modelling1')
    npname = npname.replace('JES_PURho_TAU_GG_2012', 'JES_2012_PileRho_TAU_GG')
    npname = npname.replace('JES_PURho_TAU_QG_2012', 'JES_2012_PileRho_TAU_QG')
    npname = npname.replace('JES_PURho_TAU_QQ_2012', 'JES_2012_PileRho_TAU_QQ')
    npname = npname.replace('FAKERATE_2012', 'TAU_JFAKE_2012')
    npname = npname.replace('TAUID_2012', 'TAU_ID_2012')
    npname = npname.replace('ISOL_2012', 'ANA_EMB_ISOL')
    npname = npname.replace('MFS_2012', 'ANA_EMB_MFS')
    npname = npname.replace('MET_RESOSOFTTERMS_2012', 'MET_RESOSOFT')
    npname = npname.replace('MET_SCALESOFTTERMS_2012', 'MET_SCALESOFT')
    from .ztautau import Embedded_Ztautau
    if isinstance(sample, Embedded_Ztautau):
        npname = npname.replace('TRIGGER', 'TRIGGER_EMB_HH')
    else:
        npname = npname.replace('TRIGGER', 'TRIGGER_HH')
    return npname


class Sample(object):

    def weight_fields(self):
        return []

    def __init__(self, year, scale=1., cuts=None,
                 student=DEFAULT_STUDENT,
                 trigger=True,
                 name='Sample',
                 label='Sample',
                 **hist_decor):
        self.year = year
        if year == 2011:
            self.energy = 7
        else:
            self.energy = 8
        self.scale = scale
        if cuts is None:
            self._cuts = Cut()
        else:
            self._cuts = cuts
        self.student = student
        self.name = name
        self.label = label
        self.hist_decor = hist_decor
        if 'fillstyle' not in hist_decor:
            self.hist_decor['fillstyle'] = 'solid'
        self.trigger = trigger

    def decorate(self, name=None, label=None, **hist_decor):
        if name is not None:
            self.name = name
        if label is not None:
            self.label = label
        if hist_decor:
            self.hist_decor.update(hist_decor)
        return self

    def get_field_hist(self, vars, category, templates=None):
        """
        retrieve a dictionnary of histograms for the requested
        variables in the given category
        ------
        Parameters:
        - vars: dictionnary of variables (see variables.py)
        - category: Analysis category see categories/*
        - template: dictionnary of Histograms. If specified used those
        histograms as templates to retrieve the various fields. If not
        specified, take the default binning specified in variables.py
        """

        field_hist = {}
        field_scale = {}
        for field, var_info in vars.items():
            if templates is not None and field in templates:
                field_hist[field] = templates[field].Clone(
                    title=self.label, **self.hist_decor)
                continue
            elif isinstance(var_info, _HistBase):
                field_hist[field] = var_info.Clone(
                    title=self.label, **self.hist_decor)
                continue
            bins = var_info['bins']
            if isinstance(bins, (list, tuple)):
                hist = Hist(bins,
                    title=self.label,
                    type='D',
                    **self.hist_decor)
            else:
                _range = var_info['range']
                if isinstance(_range, dict):
                    _range = _range.get(category.name.upper(), _range[None])
                if len(_range) == 3:
                    bins, min, max = _range
                else:
                    min, max = _range
                hist = Hist(bins, min, max,
                    title=self.label,
                    type='D',
                    **self.hist_decor)
            field_hist[field] = hist
            if 'scale' in var_info and var_info['scale'] != 1.:
                field_scale[field] = var_info['scale']
        return field_hist, field_scale

    def get_hist_array(self,
            field_hist_template,
            category, region,
            cuts=None,
            clf=None,
            scores=None,
            min_score=None,
            max_score=None,
            systematics=False,
            systematics_components=None,
            suffix=None,
            field_scale=None,
            weight_hist=None,
            weighted=True,
            bootstrap_data=False):

        do_systematics = (isinstance(self, SystematicsSample)
                          and self.systematics
                          and systematics)
        if do_systematics and systematics_components is None:
            systematics_components = self.systematics_components()
        if min_score is None:
            min_score = getattr(category, 'workspace_min_clf', None)
        if max_score is None:
            max_score = getattr(category, 'workspace_max_clf', None)

        histname = 'hh_category_{0}_{1}'.format(category.name, self.name)
        if suffix is not None:
            histname += suffix

        field_hist = {}
        for field, hist in field_hist_template.items():
            new_hist = hist.Clone(name=histname + '_{0}'.format(field))
            new_hist.Reset()
            field_hist[field] = new_hist

        self.draw_array(field_hist, category, region,
            cuts=cuts,
            weighted=weighted,
            field_scale=field_scale,
            weight_hist=weight_hist,
            clf=clf,
            min_score=min_score,
            max_score=max_score,
            systematics=do_systematics,
            systematics_components=systematics_components,
            bootstrap_data=bootstrap_data)

        return field_hist

    def get_hist(self,
            hist_template,
            expr_or_clf,
            category, region,
            cuts=None,
            clf=None,
            scores=None,
            min_score=None,
            max_score=None,
            systematics=False,
            systematics_components=None,
            suffix=None,
            field_scale=None,
            weight_hist=None,
            weighted=True):

        do_systematics = (isinstance(self, SystematicsSample)
                          and self.systematics
                          and systematics)
        if do_systematics and systematics_components is None:
            systematics_components = self.systematics_components()

        if min_score is None:
            min_score = getattr(category, 'workspace_min_clf', None)
        if max_score is None:
            max_score = getattr(category, 'workspace_max_clf', None)

        histname = 'hh_category_{0}_{1}'.format(category.name, self.name)
        if suffix is not None:
            histname += suffix
        hist = hist_template.Clone(name=histname,
                                   title=self.label,
                                   **self.hist_decor)
        hist.Reset()

        if isinstance(expr_or_clf, (basestring, tuple, list)):
            expr = expr_or_clf
            field_hist = dict()
            field_hist[expr] = hist
            self.draw_array(field_hist, category, region,
                cuts=cuts,
                weighted=weighted,
                field_scale=field_scale,
                weight_hist=weight_hist,
                clf=clf,
                min_score=min_score,
                max_score=max_score,
                systematics=do_systematics,
                systematics_components=systematics_components)

        else:
            # histogram classifier output
            if scores is None:
                scores = self.scores(
                    expr_or_clf, category, region, cuts,
                    systematics=do_systematics,
                    systematics_components=systematics_components)
            histogram_scores(
                hist, scores,
                min_score=min_score,
                max_score=max_score,
                inplace=True)

        return hist

    def get_histfactory_sample(self,
            hist_template,
            expr_or_clf,
            category, region,
            cuts=None,
            clf=None,
            scores=None,
            min_score=None,
            max_score=None,
            systematics=False,
            suffix=None,
            field_scale=None,
            weight_hist=None,
            weighted=True,
            no_signal_fixes=False,
            ravel=True,
            uniform=False):

        from .data import Data
        from .qcd import QCD
        from .others import Others

        log.info("creating histfactory sample for {0}".format(self.name))
        if isinstance(self, Data):
            sample = histfactory.Data(self.name)
        else:
            sample = histfactory.Sample(self.name)
        hist = self.get_hist(
            hist_template,
            expr_or_clf,
            category, region,
            cuts=cuts,
            clf=clf,
            scores=scores,
            min_score=min_score,
            max_score=max_score,
            systematics=systematics,
            suffix=suffix,
            field_scale=field_scale,
            weight_hist=weight_hist,
            weighted=weighted)
        # copy of unaltered nominal hist required by QCD shape
        nominal_hist = hist.Clone()
        if ravel:
            # convert to 1D if 2D (also handles systematics if present)
            hist = ravel_hist(hist)
        if uniform:
            # convert to uniform binning
            hist = uniform_hist(hist)
        #print_hist(hist)
        # set the nominal histogram
        sample.hist = hist
        do_systematics = (not isinstance(self, Data)
                          and self.systematics
                          and systematics)
        # add systematics samples
        if do_systematics:
            SYSTEMATICS = get_systematics(self.year)
            for sys_component in self.systematics_components():
                terms = SYSTEMATICS[sys_component]
                if len(terms) == 1:
                    up_term = terms[0]
                    hist_up = hist.systematics[up_term]
                    # use nominal hist for "down" side
                    hist_down = hist
                else:
                    up_term, down_term = terms
                    hist_up = hist.systematics[up_term]
                    hist_down = hist.systematics[down_term]
                if sys_component == 'JES_FlavComp':
                    if ((isinstance(self, Signal) and self.mode == 'gg') or
                         isinstance(self, Others)):
                        sys_component += '_TAU_G'
                    else:
                        sys_component += '_TAU_Q'
                elif sys_component == 'JES_PURho':
                    if isinstance(self, Others):
                        sys_component += '_TAU_QG'
                    elif isinstance(self, Signal):
                        if self.mode == 'gg':
                            sys_component += '_TAU_GG'
                        else:
                            sys_component += '_TAU_QQ'
                npname = get_workspace_np_name(self, sys_component, self.year)
                histsys = histfactory.HistoSys(
                    npname,
                    low=hist_down,
                    high=hist_up)
                sample.AddHistoSys(histsys)
            if isinstance(self, QCD):
                high, low = self.get_shape_systematic(
                     nominal_hist,
                     expr_or_clf,
                     category, region,
                     cuts=cuts,
                     clf=clf,
                     min_score=min_score,
                     max_score=max_score,
                     suffix=suffix,
                     field_scale=field_scale,
                     weight_hist=weight_hist,
                     weighted=weighted)
                if ravel:
                    low = ravel_hist(low)
                    high = ravel_hist(high)
                if uniform:
                    low = uniform_hist(low)
                    high = uniform_hist(high)
                #log.info("QCD low shape")
                #print_hist(low)
                #log.info("QCD high shape")
                #print_hist(high)
                npname = 'ATLAS_ANA_HH_{0:d}_QCD'.format(self.year)
                if category.analysis_control and self.decouple_shape:
                    npname += '_CR'
                histsys = histfactory.HistoSys(npname, low=low, high=high)
                sample.AddHistoSys(histsys)
        if isinstance(self, Signal):
            sample.AddNormFactor('SigXsecOverSM', 0., 0., 200., False)
        elif isinstance(self, Background):
            # only activate stat error on background samples
            sample.ActivateStatError()
        if not isinstance(self, Data):
            norm_by_theory = getattr(self, 'NORM_BY_THEORY', True)
            sample.SetNormalizeByTheory(norm_by_theory)
            if norm_by_theory:
                lumi_uncert = get_lumi_uncert(self.year)
                lumi_sys = histfactory.OverallSys(
                    'ATLAS_LUMI_{0:d}'.format(self.year),
                    high=1. + lumi_uncert,
                    low=1. - lumi_uncert)
                sample.AddOverallSys(lumi_sys)
        if hasattr(self, 'histfactory') and not (
                isinstance(self, Signal) and no_signal_fixes):
            # perform sample-specific items
            self.histfactory(sample, category, systematics=do_systematics)
        return sample

    def get_histfactory_sample_array(self,
            field_hist_template,
            category, region,
            cuts=None,
            clf=None,
            scores=None,
            min_score=None,
            max_score=None,
            systematics=False,
            suffix=None,
            field_scale=None,
            weight_hist=None,
            weighted=True,
            no_signal_fixes=False,
            bootstrap_data=False,
            ravel=True,
            uniform=False):

        from .data import Data
        from .qcd import QCD
        from .others import Others
        log.info("creating histfactory samples for {0}".format(self.name))
        field_hist = self.get_hist_array(
            field_hist_template,
            category, region,
            cuts=cuts,
            clf=clf,
            scores=scores,
            min_score=min_score,
            max_score=max_score,
            systematics=systematics,
            suffix=suffix,
            field_scale=field_scale,
            weight_hist=weight_hist,
            weighted=weighted,
            bootstrap_data=bootstrap_data)
        do_systematics = (not isinstance(self, Data)
                          and self.systematics
                          and systematics)
        if isinstance(self, QCD) and do_systematics:
            qcd_shapes = self.get_shape_systematic_array(
                field_hist,
                category, region,
                cuts=cuts,
                clf=clf,
                min_score=min_score,
                max_score=max_score,
                suffix=suffix,
                field_scale=field_scale,
                weight_hist=weight_hist,
                weighted=weighted)
        field_samples = {}
        for field, hist in field_hist.items():
            if isinstance(self, Data):
                sample = histfactory.Data(self.name)
            else:
                sample = histfactory.Sample(self.name)
            # copy of unaltered nominal hist required by QCD shape
            nominal_hist = hist.Clone()
            if ravel:
                # convert to 1D if 2D (also handles systematics if present)
                hist = ravel_hist(hist)
            if uniform:
                hist = uniform_hist(hist)
            #print_hist(hist)
            # set the nominal histogram
            sample.hist = hist
            # add systematics samples
            if do_systematics:
                SYSTEMATICS = get_systematics(self.year)
                for sys_component in self.systematics_components():
                    terms = SYSTEMATICS[sys_component]
                    if len(terms) == 1:
                        up_term = terms[0]
                        hist_up = hist.systematics[up_term]
                        # use nominal hist for "down" side
                        hist_down = hist
                    else:
                        up_term, down_term = terms
                        hist_up = hist.systematics[up_term]
                        hist_down = hist.systematics[down_term]
                    if sys_component == 'JES_FlavComp':
                        if ((isinstance(self, Signal) and self.mode == 'gg') or
                             isinstance(self, Others)):
                            sys_component += '_TAU_G'
                        else:
                            sys_component += '_TAU_Q'
                    elif sys_component == 'JES_PURho':
                        if isinstance(self, Others):
                            sys_component += '_TAU_QG'
                        elif isinstance(self, Signal):
                            if self.mode == 'gg':
                                sys_component += '_TAU_GG'
                            else:
                                sys_component += '_TAU_QQ'
                    npname = get_workspace_np_name(self, sys_component, self.year)
                    histsys = histfactory.HistoSys(
                        npname,
                        low=hist_down,
                        high=hist_up)
                    sample.AddHistoSys(histsys)
                if isinstance(self, QCD):
                    high, low = qcd_shapes[field]
                    if ravel:
                        low = ravel_hist(low)
                        high = ravel_hist(high)
                    if uniform:
                        low = uniform_hist(low)
                        high = uniform_hist(high)
                    #log.info("QCD low shape")
                    #print_hist(low)
                    #log.info("QCD high shape")
                    #print_hist(high)
                    npname = 'ATLAS_ANA_HH_{0:d}_QCD'.format(self.year)
                    if category.analysis_control and self.decouple_shape:
                        npname += '_CR'
                    histsys = histfactory.HistoSys(npname, low=low, high=high)
                    sample.AddHistoSys(histsys)
            if isinstance(self, Signal):
                sample.AddNormFactor('SigXsecOverSM', 0., 0., 200., False)
            elif isinstance(self, Background):
                # only activate stat error on background samples
                sample.ActivateStatError()
            if not isinstance(self, Data):
                norm_by_theory = getattr(self, 'NORM_BY_THEORY', True)
                sample.SetNormalizeByTheory(norm_by_theory)
                if norm_by_theory:
                    lumi_uncert = get_lumi_uncert(self.year)
                    lumi_sys = histfactory.OverallSys(
                        'ATLAS_LUMI_{0:d}'.format(self.year),
                        high=1. + lumi_uncert,
                        low=1. - lumi_uncert)
                    sample.AddOverallSys(lumi_sys)
            # HACK: disable calling this on signal for now since while plotting
            # we only want to show the combined signal but in the histfactory
            # method we require only a single mode
            if hasattr(self, 'histfactory') and not (
                    isinstance(self, Signal) and no_signal_fixes):
                # perform sample-specific items
                self.histfactory(sample, category, systematics=do_systematics)
            field_samples[field] = sample
        return field_samples

    def partitioned_records(self,
              category=None,
              region=None,
              fields=None,
              cuts=None,
              include_weight=True,
              systematic='NOMINAL',
              key=None,
              num_partitions=2,
              return_idx=False):
        """
        Partition sample into num_partitions chunks of roughly equal size
        assuming no correlation between record index and field values.
        """
        partitions = []
        for start in range(num_partitions):
            if key is None:
                # split by index
                log.info("splitting records by index parity")
                recs = self.records(
                    category=category,
                    region=region,
                    fields=fields,
                    include_weight=include_weight,
                    cuts=cuts,
                    systematic=systematic,
                    return_idx=return_idx,
                    start=start,
                    step=num_partitions)
            else:
                # split by field values modulo the number of partitions
                partition_cut = Cut('((abs({0})%{1})>={2})&&((abs({0})%{1})<{3})'.format(
                    key, num_partitions, start, start + 1))
                log.info(
                    "splitting records by key parity: {0}".format(
                        partition_cut))
                recs = self.records(
                    category=category,
                    region=region,
                    fields=fields,
                    include_weight=include_weight,
                    cuts=partition_cut & cuts,
                    systematic=systematic,
                    return_idx=return_idx)
            if return_idx:
                partitions.append(recs)
            else:
                partitions.append(np.hstack(recs))
        return partitions

    def merged_records(self,
              category=None,
              region=None,
              fields=None,
              cuts=None,
              clf=None,
              clf_name='classifier',
              include_weight=True,
              systematic='NOMINAL'):
        recs = self.records(
            category=category,
            region=region,
            fields=fields,
            include_weight=include_weight,
            cuts=cuts,
            systematic=systematic)
        if include_weight and fields is not None:
            if 'weight' not in fields:
                fields = list(fields) + ['weight']
        rec = stack(recs, fields=fields)
        if clf is not None:
            scores, _ = clf.classify(
                self, category, region,
                cuts=cuts, systematic=systematic)
            rec = recfunctions.rec_append_fields(rec,
                names=clf_name,
                data=scores,
                dtypes='f4')
        return rec

    def array(self,
              category=None,
              region=None,
              fields=None,
              cuts=None,
              clf=None,
              clf_name='classifer',
              include_weight=True,
              systematic='NOMINAL'):
        return rec2array(self.merged_records(
            category=category,
            region=region,
            fields=fields,
            cuts=cuts,
            clf=clf,
            clf_name=clf_name,
            include_weight=include_weight,
            systematic=systematic))

    def weights(self, systematic='NOMINAL'):
        weight_fields = self.weight_fields()
        if isinstance(self, SystematicsSample):
            systerm, variation = SystematicsSample.get_sys_term_variation(
                systematic)
            for term, variations in self.weight_systematics().items():
                if term == systerm:
                    weight_fields += variations[variation]
                else:
                    weight_fields += variations['NOMINAL']
        # HACK
        if not self.trigger and 'tau1_trigger_sf' in weight_fields:
            weight_fields.remove('tau1_trigger_sf')
            weight_fields.remove('tau2_trigger_sf')
            weight_fields.extend(['tau1_trigger_eff', 'tau2_trigger_eff'])
        return weight_fields

    def cuts(self, category=None, region=None, systematic='NOMINAL', **kwargs):
        cuts = Cut(self._cuts)
        if category is not None:
            cuts &= category.get_cuts(self.year, **kwargs)
        if region is not None:
            cuts &= REGIONS[region]
        if self.trigger:
            cuts &= Cut('trigger')
        if isinstance(self, SystematicsSample):
            systerm, variation = SystematicsSample.get_sys_term_variation(
                systematic)
            for term, variations in self.cut_systematics().items():
                if term == systerm:
                    cuts &= variations[variation]
                else:
                    cuts &= variations['NOMINAL']
        return cuts

    def draw(self, expr, category, region, bins, min, max,
             cuts=None, weighted=True, systematics=False):

        hist = Hist(bins, min, max,
            title=self.label,
            type='D',
            **self.hist_decor)
        self.draw_into(hist, expr, category, region,
                       cuts=cuts, weighted=weighted,
                       systematics=systematics)
        return hist

    def draw2d(self, expr, category, region,
               xbins, xmin, xmax,
               ybins, ymin, ymax,
               cuts=None,
               systematics=False,
               ravel=False):

        hist = Hist2D(xbins, xmin, xmax, ybins, ymin, ymax,
            title=self.label,
            type='D',
            **self.hist_decor)
        self.draw_into(hist, expr, category, region, cuts=cuts,
                systematics=systematics)
        if ravel:
            rhist = hist.ravel()
            if hasattr(hist, 'sytematics'):
                rhist.systematics = {}
                for term, syshist in hist.systematics.items():
                    rhist.systematics[term] = syshist.ravel()
            return rhist
        return hist

    def draw_array_helper(self, field_hist, category, region,
                          cuts=None,
                          weighted=True,
                          field_scale=None,
                          weight_hist=None,
                          field_weight_hist=None,
                          scores=None,
                          clf=None,
                          min_score=None,
                          max_score=None,
                          systematic='NOMINAL',
                          bootstrap_data=False):

        from .data import Data, DataInfo

        all_fields = []
        classifiers = []
        for f in field_hist.iterkeys():
            if isinstance(f, basestring):
                all_fields.append(f)
            elif isinstance(f, Classifier):
                classifiers.append(f)
            else:
                all_fields.extend(list(f))
        if len(classifiers) > 1:
            raise RuntimeError(
                "more than one classifier in fields is not supported")
        elif len(classifiers) == 1:
            classifier = classifiers[0]
        else:
            classifier = None

        if isinstance(self, Data) and bootstrap_data:
            log.info("using bootstrapped data")
            analysis = bootstrap_data
            recs = []
            scores = []
            for s in analysis.backgrounds:
                rec = s.merged_records(category, region,
                    fields=all_fields, cuts=cuts,
                    include_weight=True,
                    clf=clf,
                    systematic=systematic)
                recs.append(rec)
            b_rec = stack(recs, fields=all_fields + ['classifier', 'weight'])
            s_rec = analysis.higgs_125.merged_records(category, region,
                fields=all_fields, cuts=cuts,
                include_weight=True,
                clf=clf,
                systematic=systematic)

            # handle negative weights separately
            b_neg = b_rec[b_rec['weight'] < 0]
            b_pos = b_rec[b_rec['weight'] >= 0]

            def bootstrap(rec):
                prob = np.abs(rec['weight'])
                prob = prob / prob.sum()
                # random sample without replacement
                log.warning(str(int(round(abs(rec['weight'].sum())))))
                sample_idx = np.random.choice(
                    rec.shape[0], size=int(round(abs(rec['weight'].sum()))),
                    replace=False, p=prob)
                return rec[sample_idx]

            rec = stack([
                bootstrap(b_neg),
                bootstrap(b_pos),
                bootstrap(s_rec)],
                fields=all_fields + ['classifier', 'weight'])

            rec['weight'][:] = 1.
            scores = rec['classifier']
        else:
            # TODO: only get unblinded vars
            rec = self.merged_records(category, region,
                fields=all_fields, cuts=cuts,
                include_weight=True,
                clf=classifier,
                systematic=systematic)

        if isinstance(scores, tuple):
            # sanity
            #assert (scores[1] == rec['weight']).all()
            # ignore the score weights since they should be the same as the rec
            # weights
            scores = scores[0]

        weights = rec['weight']

        if scores is not None:
            if min_score is not None:
                # cut below a minimum classifier score
                idx = scores > min_score
                rec = rec[idx]
                weights = weights[idx]
                scores = scores[idx]
            if max_score is not None:
                # cut above a maximum classifier score
                idx = scores < max_score
                rec = rec[idx]
                weights = weights[idx]
                scores = scores[idx]

        def get_weight(array, hist):
            edges = np.array(list(hist.xedges()))
            # handle overflow
            edges[0] -= 1E100
            edges[-1] += 1E100
            return np.array(list(hist.y())).take(
                edges.searchsorted(array) - 1)

        if weight_hist is not None and scores is not None:
            # apply weight according to the classifier score
            log.warning("applying a score weight histogram")
            weights *= get_weight(scores, weight_hist)

        if field_weight_hist is not None:
            # apply weight corrections according to certain fields
            for field, hist in field_weight_hist.items():
                log.warning(
                    "applying a weight histogram with field {0}".format(field))
                if field not in field_hist:
                    raise ValueError(
                        "attempting to apply a weight histogram using "
                        "field {0} but that field is not present in the "
                        "requested array")
                weights *= get_weight(rec[field], hist)

        for fields, hist in field_hist.items():
            if isinstance(fields, Classifier):
                fields = ['classifier']
            # fields can be a single field or list of fields
            elif not isinstance(fields, (list, tuple)):
                fields = [fields]
            if hist is None:
                # this var might be blinded
                continue
            # defensive copy
            if isinstance(fields, tuple):
                # select columns in numpy recarray with a list
                fields = list(fields)
            arr = np.copy(rec[fields])
            if field_scale is not None:
                for field in fields:
                    if field in field_scale:
                        arr[field] *= field_scale[field]
            # convert to array
            arr = rec2array(arr, fields=fields)
            # HACK HACK HACK
            _weights = weights
            if fields == ['dEta_jets']:
                log.warning("HACK HACK")
                nonzero = arr > 0
                arr = arr[nonzero]
                _weights = weights[nonzero]
            # include the scores if the histogram dimensionality allows
            if scores is not None and hist.GetDimension() == len(fields) + 1:
                arr = np.c_[arr, scores]
            elif hist.GetDimension() != len(fields):
                raise TypeError(
                    'histogram dimensionality does not match '
                    'number of fields: %s' % (', '.join(fields)))
            hist.fill_array(arr, weights=_weights)
            if isinstance(self, Data):
                if hasattr(hist, 'datainfo'):
                    hist.datainfo += self.info
                else:
                    hist.datainfo = DataInfo(self.info.lumi, self.info.energies)


class Signal(object):
    # mixin
    pass


class Background(object):
    # mixin
    pass


class SystematicsSample(Sample):

    @classmethod
    def get_sys_term_variation(cls, systematic):
        if systematic == 'NOMINAL':
            systerm = None
            variation = 'NOMINAL'
        elif len(systematic) > 1:
            # no support for this yet...
            systerm = None
            variation = 'NOMINAL'
        else:
            systerm, variation = systematic[0].rsplit('_', 1)
        return systerm, variation

    def systematics_components(self):
        common = [
            'MET_RESOSOFTTERMS',
            'MET_SCALESOFTTERMS',
            'TAUID',
            'TRIGGER',
            'FAKERATE',
        ]
        if self.year == 2011:
            return common + [
                'TES_TRUE_FINAL',
                'TES_FAKE_FINAL',
            ]
        else:
            return common + [
                'TES_TRUE_INSITUINTERPOL',
                'TES_TRUE_SINGLEPARTICLEINTERPOL',
                'TES_TRUE_MODELING',
                'TES_FAKE_TOTAL',
            ]

    def weight_systematics(self):
        return {
            'FAKERATE': {
                'UP': [
                    'tau1_fakerate_sf_high',
                    'tau2_fakerate_sf_high'],
                'DOWN': [
                    'tau1_fakerate_sf_low',
                    'tau2_fakerate_sf_low'],
                'NOMINAL': [
                    'tau1_fakerate_sf',
                    'tau2_fakerate_sf']},
            'TAUID': {
                'UP': [
                    'tau1_id_sf_high',
                    'tau2_id_sf_high'],
                'DOWN': [
                    'tau1_id_sf_low',
                    'tau2_id_sf_low'],
                'NOMINAL': [
                    'tau1_id_sf',
                    'tau2_id_sf']},
        }

    def cut_systematics(self):
        return {}

    def __init__(self, year, db=DB, systematics=False, **kwargs):

        if isinstance(self, Background):
            sample_key = self.__class__.__name__.lower()
            sample_info = samples_db.get_sample(
                'hadhad', year, 'background', sample_key)
            kwargs.setdefault('name', sample_info['name'])
            kwargs.setdefault('label', sample_info['root'])
            if 'color' in sample_info and 'color' not in kwargs:
                kwargs['color'] = sample_info['color']
            self.samples = sample_info['samples']

        elif isinstance(self, Signal):
            # samples already defined in Signal subclass
            # see Higgs class below
            assert len(self.samples) > 0

        else:
            raise TypeError(
                'MC sample %s does not inherit from Signal or Background' %
                self.__class__.__name__)

        super(SystematicsSample, self).__init__(year=year, **kwargs)

        self.db = db
        self.datasets = []
        self.systematics = systematics
        rfile = get_file(self.student)
        h5file = get_file(self.student, hdf=True)

        from .ztautau import Embedded_Ztautau

        for i, name in enumerate(self.samples):

            ds = self.db[name]
            treename = name.replace('.', '_')
            treename = treename.replace('-', '_')

            trees = {}
            tables = {}
            weighted_events = {}

            if isinstance(self, Embedded_Ztautau):
                events_bin = 1
            else:
                # use mc_weighted second bin
                events_bin = 2
            events_hist_suffix = '_cutflow'

            trees['NOMINAL'] = rfile[treename]
            tables['NOMINAL'] =  CachedTable.hook(getattr(
                h5file.root, treename))
            cutflow_hist = rfile[treename + events_hist_suffix]
            weighted_events['NOMINAL'] = cutflow_hist[events_bin].value
            del cutflow_hist

            if self.systematics:

                systematics_terms, systematics_samples = \
                    samples_db.get_systematics('hadhad', self.year, name)

                if systematics_terms:
                    for sys_term in systematics_terms:
                        sys_name = treename + '_' + '_'.join(sys_term)
                        trees[sys_term] = rfile[sys_name]
                        tables[sys_term] = CachedTable.hook(getattr(
                            h5file.root, sys_name))
                        cutflow_hist = rfile[sys_name + events_hist_suffix]
                        weighted_events[sys_term] = cutflow_hist[events_bin].value
                        del cutflow_hist

                if systematics_samples:
                    for sample_name, sys_term in systematics_samples.items():
                        log.info("%s -> %s %s" % (name, sample_name, sys_term))
                        sys_term = tuple(sys_term.split(','))
                        sys_ds = self.db[sample_name]
                        sample_name = sample_name.replace('.', '_')
                        sample_name = sample_name.replace('-', '_')
                        trees[sys_term] = rfile[sample_name]
                        tables[sys_term] = CachedTable.hook(getattr(
                            h5file.root, sample_name))
                        cutflow_hist = rfile[sample_name + events_hist_suffix]
                        weighted_events[sys_term] = cutflow_hist[events_bin].value
                        del cutflow_hist

            if hasattr(self, 'xsec_kfact_effic'):
                xs, kfact, effic = self.xsec_kfact_effic(i)
            else:
                xs, kfact, effic = ds.xsec_kfact_effic
            log.debug(
                "dataset: {0}  cross section: {1} [pb] "
                "k-factor: {2} "
                "filtering efficiency: {3} "
                "events {4}".format(
                    ds.name, xs, kfact, effic, weighted_events['NOMINAL']))
            self.datasets.append(
                (ds, trees, tables, weighted_events, xs, kfact, effic))

    def draw_into(self, hist, expr, category, region,
                  cuts=None,
                  weighted=True,
                  systematics=False,
                  systematics_components=None,
                  scale=1.):

        from .ztautau import Ztautau

        if isinstance(expr, (list, tuple)):
            exprs = expr
        else:
            exprs = (expr,)

        do_systematics = self.systematics and systematics

        if do_systematics:
            if hasattr(hist, 'systematics'):
                sys_hists = hist.systematics
            else:
                sys_hists = {}

        selection = self.cuts(category, region) & cuts

        for ds, sys_trees, _, sys_events, xs, kfact, effic in self.datasets:
            log.debug(ds.name)
            nominal_tree = sys_trees['NOMINAL']
            nominal_events = sys_events['NOMINAL']
            nominal_weight = (
                LUMI[self.year] *
                scale * self.scale *
                xs * kfact * effic / nominal_events)
            nominal_weighted_selection = (
                '%f * %s * (%s)' %
                (nominal_weight,
                 '*'.join(map(str,
                     self.weights('NOMINAL'))),
                 selection))
            log.debug(nominal_weighted_selection)
            current_hist = hist.Clone()
            current_hist.Reset()
            # fill nominal histogram
            for expr in exprs:
                nominal_tree.Draw(expr, nominal_weighted_selection,
                                  hist=current_hist)
            hist += current_hist
            if not do_systematics:
                continue
            # iterate over systematic variations skipping the nominal
            for sys_term in iter_systematics(False,
                    year=self.year,
                    components=systematics_components):
                sys_hist = current_hist.Clone()
                if sys_term in sys_trees:
                    sys_tree = sys_trees[sys_term]
                    sys_event = sys_events[sys_term]
                    sys_hist.Reset()
                    sys_weight = (
                        LUMI[self.year] *
                        scale * self.scale *
                        xs * kfact * effic / sys_event)
                    sys_weighted_selection = (
                        '%f * %s * (%s)' %
                        (sys_weight,
                         ' * '.join(map(str,
                             self.weights('NOMINAL'))),
                         selection))
                    log.debug(sys_weighted_selection)
                    for expr in exprs:
                        sys_tree.Draw(expr, sys_weighted_selection, hist=sys_hist)
                else:
                    log.debug(
                        "tree for %s not present for %s "
                        "using NOMINAL" % (sys_term, ds.name))
                    # QCD + Ztautau fit error
                    if isinstance(self, Ztautau):
                        if sys_term == ('ZFIT_UP',):
                            sys_hist *= (
                                    (self.scale + self.scale_error) /
                                    self.scale)
                        elif sys_term == ('ZFIT_DOWN',):
                            sys_hist *= (
                                    (self.scale - self.scale_error) /
                                    self.scale)
                if sys_term not in sys_hists:
                    sys_hists[sys_term] = sys_hist
                else:
                    sys_hists[sys_term] += sys_hist
            # iterate over weight systematics on the nominal tree
            for weight_branches, sys_term in self.iter_weight_branches():
                sys_hist = current_hist.Clone()
                sys_hist.Reset()
                weighted_selection = (
                    '%f * %s * (%s)' %
                    (nominal_weight,
                     ' * '.join(map(str, weight_branches)),
                     selection))
                log.debug(weighted_selection)
                for expr in exprs:
                    nominal_tree.Draw(expr, weighted_selection, hist=sys_hist)
                if sys_term not in sys_hists:
                    sys_hists[sys_term] = sys_hist
                else:
                    sys_hists[sys_term] += sys_hist
        if do_systematics:
            # set the systematics
            hist.systematics = sys_hists

    def draw_array(self, field_hist, category, region,
                   cuts=None,
                   weighted=True,
                   field_scale=None,
                   weight_hist=None,
                   field_weight_hist=None,
                   clf=None,
                   scores=None,
                   min_score=None,
                   max_score=None,
                   systematics=False,
                   systematics_components=None,
                   scale=1.,
                   bootstrap_data=False):

        do_systematics = self.systematics and systematics

        if scores is None and clf is not None:
            scores = self.scores(
                clf, category, region, cuts=cuts,
                systematics=systematics,
                systematics_components=systematics_components)

        self.draw_array_helper(field_hist, category, region,
            cuts=cuts,
            weighted=weighted,
            field_scale=field_scale,
            weight_hist=weight_hist,
            field_weight_hist=field_weight_hist,
            scores=scores['NOMINAL'] if scores else None,
            min_score=min_score,
            max_score=max_score,
            systematic='NOMINAL')

        if not do_systematics:
            return

        all_sys_hists = {}

        for field, hist in field_hist.items():
            if not hasattr(hist, 'systematics'):
                hist.systematics = {}
            all_sys_hists[field] = hist.systematics

        for systematic in iter_systematics(False,
                year=self.year,
                components=systematics_components):

            sys_field_hist = {}
            for field, hist in field_hist.items():
                if systematic in all_sys_hists[field]:
                    sys_hist = all_sys_hists[field][systematic]
                else:
                    sys_hist = hist.Clone(
                        name=hist.name + '_' + systematic_name(systematic))
                    sys_hist.Reset()
                    all_sys_hists[field][systematic] = sys_hist
                sys_field_hist[field] = sys_hist

            self.draw_array_helper(sys_field_hist, category, region,
                cuts=cuts,
                weighted=weighted,
                field_scale=field_scale,
                weight_hist=weight_hist,
                field_weight_hist=field_weight_hist,
                scores=scores[systematic] if scores else None,
                min_score=min_score,
                max_score=max_score,
                systematic=systematic)


    def scores(self, clf, category, region,
               cuts=None, scores_dict=None,
               systematics=False,
               systematics_components=None,
               scale=1.):

        # TODO check that weight systematics are included
        do_systematics = self.systematics and systematics
        if scores_dict is None:
            scores_dict = {}
        for systematic in iter_systematics(True,
                year=self.year,
                components=systematics_components):
            if not do_systematics and systematic != 'NOMINAL':
                continue
            scores, weights = clf.classify(self,
                category=category,
                region=region,
                cuts=cuts,
                systematic=systematic)
            weights *= scale
            if systematic not in scores_dict:
                scores_dict[systematic] = (scores, weights)
            else:
                prev_scores, prev_weights = scores_dict[systematic]
                scores_dict[systematic] = (
                    np.concatenate((prev_scores, scores)),
                    np.concatenate((prev_weights, weights)))
        return scores_dict

    def trees(self, category, region,
              cuts=None, systematic='NOMINAL',
              scale=1.):

        from .ztautau import Ztautau

        TEMPFILE.cd()
        selection = self.cuts(category, region) & cuts
        weight_branches = self.weights(systematic)
        if systematic in SYSTEMATICS_BY_WEIGHT:
            systematic = 'NOMINAL'

        trees = []
        for ds, sys_trees, _, sys_events, xs, kfact, effic in self.datasets:

            try:
                tree = sys_trees[systematic]
                events = sys_events[systematic]
            except KeyError:
                log.debug(
                    "tree for %s not present for %s "
                    "using NOMINAL" % (systematic, ds.name))
                tree = sys_trees['NOMINAL']
                events = sys_events['NOMINAL']

            actual_scale = self.scale
            if isinstance(self, Ztautau):
                if systematic == ('ZFIT_UP',):
                    actual_scale = self.scale + self.scale_error
                elif systematic == ('ZFIT_DOWN',):
                    actual_scale = self.scale - self.scale_error

            weight = (
                scale * actual_scale *
                LUMI[self.year] *
                xs * kfact * effic / events)

            selected_tree = asrootpy(tree.CopyTree(selection))
            log.debug("{0} {1}".format(selected_tree.GetEntries(), weight))
            selected_tree.SetWeight(weight)
            selected_tree.userdata.weight_branches = weight_branches
            log.debug("{0} {1} {2}".format(
                self.name, selected_tree.GetEntries(),
                selected_tree.GetWeight()))
            trees.append(selected_tree)
        return trees

    def records(self,
                category=None,
                region=None,
                fields=None,
                cuts=None,
                include_weight=True,
                systematic='NOMINAL',
                scale=1.,
                return_idx=False,
                **kwargs):

        from .ztautau import Ztautau
        if include_weight and fields is not None:
            if 'weight' not in fields:
                fields = list(fields) + ['weight']
        selection = self.cuts(category, region, systematic) & cuts
        table_selection = selection.where()
        if systematic == 'NOMINAL':
            log.info("requesting table from %s" %
                     (self.__class__.__name__))
        else:
            log.info("requesting table from %s for systematic %s " %
                     (self.__class__.__name__, systematic_name(systematic)))
        log.debug("using selection: %s" % selection)
        weight_branches = self.weights(systematic)
        if systematic in SYSTEMATICS_BY_WEIGHT:
            systematic = 'NOMINAL'
        recs = []
        if return_idx:
            idxs = []
        for ds, _, sys_tables, sys_events, xs, kfact, effic in self.datasets:
            try:
                table = sys_tables[systematic]
                events = sys_events[systematic]
            except KeyError:
                log.debug(
                    "table for %s not present for %s "
                    "using NOMINAL" % (systematic, ds.name))
                table = sys_tables['NOMINAL']
                events = sys_events['NOMINAL']
            log.debug(
                "dataset: {0}  cross section: {1} [pb] "
                "k-factor: {2} "
                "filtering efficiency: {3} "
                "events {4}".format(
                    ds.name, xs, kfact, effic, events))
            actual_scale = self.scale
            if isinstance(self, Ztautau):
                if systematic == ('ZFIT_UP',):
                    log.debug("scaling up for ZFIT_UP")
                    actual_scale += self.scale_error
                elif systematic == ('ZFIT_DOWN',):
                    log.debug("scaling down for ZFIT_DOWN")
                    actual_scale -= self.scale_error
            weight = (
                scale * actual_scale *
                LUMI[self.year] *
                xs * kfact * effic / events)
            # read the table with a selection
            try:
                if table_selection:
                    rec = table.read_where(table_selection, **kwargs)
                else:
                    rec = table.read(**kwargs)
            except Exception as e:
                print table
                print e
                continue
                #raise
            if return_idx:
                # only valid if table_selection is non-empty
                idx = table.get_where_list(table_selection, **kwargs)
                idxs.append(idx)
            # add weight field
            if include_weight:
                weights = np.empty(rec.shape[0], dtype='f8')
                weights.fill(weight)
                # merge the weight fields
                weights *= reduce(np.multiply,
                    [rec[br] for br in weight_branches])
                # drop other weight fields
                #rec = recfunctions.rec_drop_fields(rec, weight_branches)
                # add the combined weight
                rec = recfunctions.rec_append_fields(rec,
                    names='weight',
                    data=weights,
                    dtypes='f8')
                if rec['weight'].shape[0] > 1 and rec['weight'].sum() == 0:
                    log.warning("{0}: weights sum to zero!".format(table.name))
            if fields is not None:
                try:
                    rec = rec[fields]
                except Exception as e:
                    print table
                    print rec.shape
                    print rec.dtype
                    print e
                    raise
            recs.append(rec)
        if return_idx:
            return zip(recs, idxs)
        return recs

    def events(self, category=None, region=None,
               cuts=None,
               systematic='NOMINAL',
               weighted=True,
               hist=None,
               scale=1.):
        if hist is None:
            hist = Hist(1, -100, 100)
        for ds, sys_trees, _, sys_events, xs, kfact, effic in self.datasets:
            try:
                tree = sys_trees[systematic]
                events = sys_events[systematic]
            except KeyError:
                log.debug(
                    "tree for %s not present for %s "
                    "using NOMINAL" % (systematic, ds.name))
                tree = sys_trees['NOMINAL']
                events = sys_events['NOMINAL']
            if weighted:
                weight = LUMI[self.year] * self.scale * xs * kfact * effic / events
                selection = Cut(' * '.join(map(str,
                    self.weights(systematic))))
                selection = Cut(str(weight * scale)) * selection * (
                    self.cuts(category, region, systematic=systematic) & cuts)
            else:
                selection = self.cuts(category, region, systematic=systematic) & cuts
            log.debug("requesing number of events from %s using cuts: %s"
                % (tree.GetName(), selection))
            tree.Draw('1', selection, hist=hist)
        return hist


class MC(SystematicsSample):

    def systematics_components(self):
        components = super(MC, self).systematics_components()
        return components + [
            'JES_Modelling',
            'JES_Detector',
            'JES_EtaModelling',
            'JES_EtaMethod',
            'JES_PURho',
            'JES_FlavComp',
            'JES_FlavResp',
            #'JVF',
            'JER',
        ]

    def weight_fields(self):
        return super(MC, self).weight_fields() + [
            'mc_weight',
            'pileup_weight',
            'ggf_weight',
        ]

    def weight_systematics(self):
        systematics = super(MC, self).weight_systematics()
        systematics.update({
            'TRIGGER': {
                'UP': [
                    'tau1_trigger_sf_high',
                    'tau2_trigger_sf_high'],
                'DOWN': [
                    'tau1_trigger_sf_low',
                    'tau2_trigger_sf_low'],
                'NOMINAL': [
                    'tau1_trigger_sf',
                    'tau2_trigger_sf']}})
        return systematics



class CompositeSample(object):
    """
    This class adds together the events from a list of samples
    and also return the summed histograms of all of those samples
    for the requested fields
    TODO: Implement a naming from the components.
    """

    def __init__(self, samples_list, name='Sample', label='Sample'):
        if not isinstance( samples_list, (list,tuple)):
            samples_list = [samples_list]
        if not isinstance (samples_list[0], Sample):
            raise ValueError( "samples_list must be filled with Samples")
        self.samples_list = samples_list
        self.name = name
        self.label = label

    def events(self, *args,**kwargs ):
        """
        Return a one-bin histogram with the total sum of events
        of all the samples
        Parameters:
        - See the events() method in the Sample class
        """
        return sum([s.events(*args, **kwargs) for s in self.samples_list])

    def draw_array(self, field_hist_tot, category, region, systematics=False, **kwargs):
        """
        Construct histograms of the sum of all the samples.
        Parameters:
        - field_hist_tot: dictionnary of Histograms that constain the structure we want to retrieve
        - category: the analysis category
        - region: the analysis region (for example 'OS')
        - systematics: boolean flag
        """

        field_hists_list = []
        # -------- Retrieve the histograms dictionnary from each sample and store it into a list
        for s in self.samples_list:
            # field_hists_temp = s.get_hist_array( field_hist_tot, category, region, systematics=systematics,**kwargs)
            field_hists_temp = {}
            for field,hist in field_hist_tot.items():
                field_hists_temp[field] = hist.Clone()
                field_hists_temp[field].Reset()
            s.draw_array( field_hists_temp, category, region, systematics=systematics,**kwargs)
            field_hists_list.append( field_hists_temp )

        # -------- Reset the output histograms
        for field, hist in field_hists_list[0].items():
            hist_tot = hist.Clone()
            hist_tot.Reset()
            field_hist_tot[field] = hist_tot

        # -------- Add the nominal histograms
        for field_hist in field_hists_list:
            for field, hist in field_hist.items():
                field_hist_tot[field].Add( hist )

        # --- Systematic Uncertainties block
        if systematics:
            #--- loop over the dictionnary of the summed histograms
            for field,hist in field_hist_tot.items():
                # --- Add a dictionary to the nominal summed histogram
                if not hasattr( hist,'systematics'):
                    hist.systematics = {}
                # --- loop over the systematic uncercainties 
                for sys in iter_systematics(self.samples_list[0].year):
                    if sys is 'NOMINAL':
                        continue
                    log.info ( "Fill the %s syst for the field %s" % (sys,field) )
                    # -- Create an histogram for each systematic uncertainty
                    hist.systematics[sys] =  hist.Clone()
                    hist.systematics[sys].Reset()
                    # -- loop over the samples and sum-up the syst-applied histograms
                    for field_hist_sample in field_hists_list:
                        field_hist_syst = field_hist_sample[field].systematics
                        hist.systematics[sys].Add( field_hist_syst[sys] )
        return
