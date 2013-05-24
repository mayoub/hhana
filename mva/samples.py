# std lib imports
import os
import sys
import atexit
from operator import add, itemgetter
import math
import warnings

# numpy imports
import numpy as np
from numpy.lib import recfunctions

# pytables imports
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import tables

# higgstautau imports
from higgstautau import datasets
from higgstautau.decorators import cached_property, memoize_method
from higgstautau import samples as samples_db

# rootpy imports
import ROOT
from rootpy.plotting import Hist, Hist2D, Canvas, HistStack
from rootpy.io import root_open as ropen, TemporaryFile
from rootpy.tree import Tree, Cut
from rootpy import asrootpy
from rootpy.memory.keepalive import keepalive

# local imports
from . import log; log = log[__name__]
from . import variables
from . import NTUPLE_PATH, DEFAULT_STUDENT
from .utils import print_hist, rec_to_ndarray
from .lumi import LUMI
from .systematics import *
from .constants import *
from .classify import histogram_scores
from .stats.histfactory import to_uniform_binning
from .cachedtable import CachedTable
from .regions import REGIONS

# Higgs cross sections
import yellowhiggs

VERBOSE = False

DB_HH = datasets.Database(name='datasets_hh', verbose=VERBOSE)
DB_TAUID = datasets.Database(name='datasets_tauid', verbose=VERBOSE)
FILES = {}


TEMPFILE = TemporaryFile()


def get_file(student, hdf=False, suffix=''):

    if hdf:
        ext = '.h5'
    else:
        ext = '.root'
    filename = student + ext
    if filename in FILES:
        return FILES[filename]
    file_path = os.path.join(NTUPLE_PATH, student + suffix, filename)
    log.info("opening %s ..." % file_path)
    if hdf:
        student_file = tables.openFile(file_path)
    else:
        student_file = ropen(file_path, 'READ')
    FILES[filename] = student_file
    return student_file


@atexit.register
def cleanup():

    TEMPFILE.Close()
    for filehandle in FILES.values():
        filehandle.close()


class Sample(object):

    WEIGHT_BRANCHES = [
        'mc_weight',
        'pileup_weight', # 2012 PROBLEM
        'ggf_weight',
    ]

    SYSTEMATICS_COMPONENTS = []

    def __init__(self, year, scale=1., cuts=None,
                 student=DEFAULT_STUDENT,
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
        self.hist_decor = hist_decor
        #if isinstance(self, Higgs):
        #    self.hist_decor['fillstyle'] = 'hollow'
        #else:
        if 'fillstyle' not in hist_decor:
            self.hist_decor['fillstyle'] = 'solid'

    def get_histfactory_sample(self, hist_template,
                               expr_or_clf,
                               category, region,
                               cuts=None,
                               scores=None,
                               systematics=True):

        log.info("creating histfactory sample for %s" % self.name)
        if isinstance(self, Data):
            sample = ROOT.RooStats.HistFactory.Data()
        else:
            sample = ROOT.RooStats.HistFactory.Sample(self.name)

        ndim = hist_template.GetDimension()
        do_systematics = (not isinstance(self, Data)
                          and self.systematics
                          and systematics)

        if isinstance(expr_or_clf, basestring):
            expr = expr_or_clf
            hist = hist_template.Clone()
            hist.Reset()
            self.draw_into(hist, expr, category, region, cuts,
                    systematics=systematics)
            if ndim > 1:
                if do_systematics:
                    syst = hist.systematics
                # convert to 1D hist
                hist = hist.ravel()
                if do_systematics:
                    hist.systematics = syst

        else:
            # histogram classifier output
            if scores is not None:
                scores = self.scores(expr_or_clf, category, region, cuts)
            hist = histogram_scores(hist_template, scores)

        # set the nominal histogram
        print_hist(hist)
        uniform_hist = to_uniform_binning(hist)
        sample.SetHisto(uniform_hist)
        keepalive(sample, uniform_hist)

        # add systematics samples
        if do_systematics:
            for sys_component in self.__class__.SYSTEMATICS_COMPONENTS:
                terms = SYSTEMATICS[sys_component]
                if len(terms) == 1:
                    up_term = terms[0]
                    down_term = terms[0]
                else:
                    up_term, down_term = terms
                log.info("adding histosys for %s" % sys_component)
                histsys = ROOT.RooStats.HistFactory.HistoSys(sys_component)

                hist_up = hist.systematics[up_term]
                hist_down = hist.systematics[down_term]

                if ndim > 1:
                    # convert to 1D hists
                    hist_up = hist_up.ravel()
                    hist_down = hist_down.ravel()

                uniform_hist_up = to_uniform_binning(hist_up)
                uniform_hist_down = to_uniform_binning(hist_down)

                histsys.SetHistoHigh(uniform_hist_up)
                histsys.SetHistoLow(uniform_hist_down)
                keepalive(histsys, uniform_hist_up, uniform_hist_down)

                sample.AddHistoSys(histsys)
                keepalive(sample, histsys)

        if isinstance(self, Signal):
            log.info("defining SigXsecOverSM POI for %s" % self.name)
            sample.AddNormFactor('SigXsecOverSM', 0., 0., 60.)
        elif isinstance(self, Background):
            # only activate stat error on background samples
            log.info("activating stat error for %s" % self.name)
            sample.ActivateStatError()

        if hasattr(self, 'histfactory'):
            # perform sample-specific items
            log.info("calling %s histfactory method" % self.name)
            self.histfactory(sample, systematics=do_systematics)

        return sample

    def partitioned_records(self,
              category,
              region,
              fields=None,
              cuts=None,
              include_weight=True,
              systematic='NOMINAL',
              num_partitions=2):
        """
        Partition sample into num_partitions chunks of roughly equal size
        assuming no correlation between record index and field values.
        """
        partitions = []
        for start in range(num_partitions):
            recs = self.records(
                category,
                region,
                fields=fields,
                include_weight=include_weight,
                cuts=cuts,
                systematic=systematic,
                start=start,
                step=num_partitions)
            partitions.append(np.hstack(recs))

        return partitions

    def merged_records(self,
              category,
              region,
              fields=None,
              cuts=None,
              include_weight=True,
              systematic='NOMINAL'):

        recs = self.records(
                category,
                region,
                fields=fields,
                include_weight=include_weight,
                cuts=cuts,
                systematic=systematic)

        return np.hstack(recs)

    def array(self,
              category,
              region,
              fields=None,
              cuts=None,
              include_weight=True,
              systematic='NOMINAL'):

        return rec_to_ndarray(self.merged_records(
            category,
            region,
            fields=fields,
            cuts=cuts,
            include_weight=include_weight,
            systematic=systematic))

    @classmethod
    def check_systematic(cls, systematic):

        if systematic != 'NOMINAL' and issubclass(cls, Data):
            raise TypeError('Do not apply systematics on data!')

    @classmethod
    def get_sys_term_variation(cls, systematic):

        Sample.check_systematic(systematic)
        if systematic == 'NOMINAL':
            systerm = None
            variation = 'NOMINAL'
        elif len(systematic) > 1:
            # no support for this yet...
            systerm = None
            variation = 'NOMINAL'
        else:
            systerm, variation = systematic[0].split('_')
        return systerm, variation

    def get_weight_branches(self, systematic,
                            no_cuts=False, only_cuts=False,
                            weighted=True):

        if not weighted:
            return ["1.0"]
        systerm, variation = Sample.get_sys_term_variation(systematic)
        if not only_cuts:
            weight_branches = Sample.WEIGHT_BRANCHES[:]
            for term, variations in WEIGHT_SYSTEMATICS.items():
                if term == systerm:
                    weight_branches += variations[variation]
                else:
                    weight_branches += variations['NOMINAL']
        else:
            weight_branches = []
        if not no_cuts and isinstance(self, Embedded_Ztautau):
            for term, variations in EMBEDDING_SYSTEMATICS.items():
                if term == systerm:
                    if variations[variation]:
                        weight_branches.append(variations[variation])
                else:
                    if variations['NOMINAL']:
                        weight_branches.append(variations['NOMINAL'])
        return weight_branches

    def iter_weight_branches(self):

        for type, variations in WEIGHT_SYSTEMATICS.items():
            for variation in variations:
                if variation == 'NOMINAL':
                    continue
                term = ('%s_%s' % (type, variation),)
                yield self.get_weight_branches(term), term
        if isinstance(self, Embedded_Ztautau):
            for type, variations in EMBEDDING_SYSTEMATICS.items():
                for variation in variations:
                    if variation == 'NOMINAL':
                        continue
                    term = ('%s_%s' % (type, variation),)
                    yield self.get_weight_branches(term), term

    def cuts(self, category, region, systematic='NOMINAL', **kwargs):

        sys_cut = Cut()
        if isinstance(self, Embedded_Ztautau):
            systerm, variation = Sample.get_sys_term_variation(systematic)
            for term, variations in EMBEDDING_SYSTEMATICS.items():
                if term == systerm:
                    sys_cut &= variations[variation]
                else:
                    sys_cut &= variations['NOMINAL']
        return (category.get_cuts(self.year, **kwargs) &
                REGIONS[region] & self._cuts & sys_cut)

    def draw(self, expr, category, region, bins, min, max,
             cuts=None, weighted=True, systematics=True):

        hist = Hist(bins, min, max, title=self.label, **self.hist_decor)
        self.draw_into(hist, expr, category, region,
                       cuts=cuts, weighted=weighted,
                       systematics=systematics)
        return hist

    def draw2d(self, expr, category, region,
               xbins, xmin, xmax,
               ybins, ymin, ymax,
               cuts=None,
               systematics=True):

        hist = Hist2D(xbins, xmin, xmax, ybins, ymin, ymax,
                title=self.label, **self.hist_decor)
        self.draw_into(hist, expr, category, region, cuts=cuts,
                systematics=systematics)
        return hist


class Data(Sample):

    def __init__(self, year, markersize=1.2, **kwargs):

        super(Data, self).__init__(
            year=year, scale=1.,
            markersize=markersize, **kwargs)
        rfile = get_file(self.student)
        h5file = get_file(self.student, hdf=True)
        dataname = 'data%d_JetTauEtmiss' % (year % 1E3)
        self.data = getattr(rfile, dataname)
        self.h5data = CachedTable.hook(getattr(h5file.root, dataname))

        self.label = ('%s Data $\sqrt{s} = %d$ TeV\n'
                      '$\int L dt = %.2f$ fb$^{-1}$' % (
                          self.year, self.energy, LUMI[self.year] / 1e3))
        self.name = 'Data'

    def events(self, category, region, cuts=None, raw=False):

        selection = self.cuts(category, region) & cuts
        log.debug("requesting number of events from %s using cuts: %s" %
                  (self.data.GetName(), selection))
        return self.data.GetEntries(selection)

    def draw_into(self, hist, expr, category, region,
                  cuts=None, weighted=True, systematics=True):

        self.data.draw(expr, self.cuts(category, region) & cuts, hist=hist)

    def draw_array(self, field_hist, category, region,
                   cuts=None, weighted=True,
                   field_scale=None,
                   weight_hist=None, weight_clf=None,
                   scores=None, systematics=True):

        # TODO: only get unblinded vars
        rec = self.merged_records(category, region,
                fields=field_hist.keys(), cuts=cuts,
                include_weight=True)

        if weight_hist is not None:
            clf_scores = self.scores(
                    weight_clf, category, region, cuts=cuts)[0]
            edges = np.array(list(weight_hist.xedges()))
            weights = np.array(weight_hist).take(edges.searchsorted(clf_scores) - 1)
            weights = rec['weight'] * weights
        else:
            weights = rec['weight']

        for field, hist in field_hist.items():
            if hist is None:
                # this var might be blinded
                continue
            if field_scale is not None and field in field_scale:
                arr = rec[field] * field_scale[field]
            else:
                arr = rec[field]
            if scores is not None:
                arr = np.c_[arr, scores]
            hist.fill_array(arr, weights=weights)

    def scores(self, clf, category, region, cuts=None):

        return clf.classify(self,
                category=category,
                region=region,
                cuts=cuts)

    def trees(self,
              category,
              region,
              cuts=None,
              systematic='NOMINAL'):

        Sample.check_systematic(systematic)
        TEMPFILE.cd()
        tree = asrootpy(self.data.CopyTree(self.cuts(category, region) & cuts))
        tree.userdata.weight_branches = []
        return [tree]

    def records(self,
                category,
                region,
                fields=None,
                cuts=None,
                include_weight=True,
                systematic='NOMINAL',
                **kwargs):

        if include_weight and fields is not None:
            if 'weight' not in fields:
                fields = fields + ['weight']

        Sample.check_systematic(systematic)
        selection = self.cuts(category, region) & cuts

        log.info("requesting table from Data %d" % self.year)
        log.debug("using selection: %s" % selection)

        # read the table with a selection
        rec = self.h5data.read_where(selection.where(), **kwargs)

        # add weight field
        if include_weight:
            # data is not weighted
            weights = np.ones(rec.shape[0], dtype='f4')
            rec = recfunctions.rec_append_fields(rec,
                    names='weight',
                    data=weights,
                    dtypes='f4')
        if fields is not None:
            rec = rec[fields]
        return [rec]

    def indices(self,
                category,
                region,
                cuts=None,
                systematic='NOMINAL',
                **kwargs):

        Sample.check_systematic(systematic)
        selection = self.cuts(category, region) & cuts

        log.info("requesting indices from Data %d" % self.year)
        log.debug("using selection: %s" % selection)

        # read the table with a selection
        idx = self.h5data.get_where_list(selection.where(), **kwargs)

        return [idx]


class Signal:
    # mixin
    pass


class Background:
    # mixin
    pass


class MC(Sample):

    # TODO: remove 'JE[S|R]' here unless embedded classes should inherit from
    # elsewhere
    SYSTEMATICS_COMPONENTS = Sample.SYSTEMATICS_COMPONENTS + [
        'JES',
        'JER',
        'TES',
        'TAUID',
        'TRIGGER',
        'FAKERATE',
    ]

    def __init__(self, year, db=DB_HH, systematics=True, **kwargs):

        if isinstance(self, Background):
            sample_key = self.__class__.__name__.lower()
            sample_info = samples_db.get_sample(
                    'hadhad', year, 'background', sample_key)
            self.name = sample_info['name']
            self._label = sample_info['latex']
            self._label_root = sample_info['root']
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

        super(MC, self).__init__(year=year, **kwargs)

        self.db = db
        self.datasets = []
        self.systematics = systematics
        rfile = get_file(self.student)
        h5file = get_file(self.student, hdf=True)

        for i, name in enumerate(self.samples):

            ds = self.db[name]
            treename = name.replace('.', '_')
            treename = treename.replace('-', '_')

            trees = {}
            tables = {}
            weighted_events = {}

            if isinstance(self, Embedded_Ztautau):
                events_bin = 0
            else:
                # use mc_weighted second bin
                events_bin = 1
            events_hist_suffix = '_cutflow'

            trees['NOMINAL'] = rfile.Get(treename)
            tables['NOMINAL'] =  CachedTable.hook(getattr(
                h5file.root, treename))

            weighted_events['NOMINAL'] = rfile.Get(
                    treename + events_hist_suffix)[events_bin]

            if self.systematics:

                systematics_terms, systematics_samples = \
                    samples_db.get_systematics('hadhad', self.year, name)

                # TODO: check that all expected systematics components are
                # included

                unused_terms = SYSTEMATICS_TERMS[:]

                if systematics_terms:
                    for sys_term in systematics_terms:

                        # merge terms such as JES_UP,TES_UP (embedding)
                        # and TES_UP (MC)
                        actual_sys_term = sys_term
                        for term in unused_terms:
                            if set(term) & set(sys_term):
                                if len(sys_term) < len(term):
                                    log.info("merging %s and %s" % (
                                        term, sys_term))
                                    sys_term = term
                                break

                        sys_name = treename + '_' + '_'.join(actual_sys_term)
                        trees[sys_term] = rfile.Get(sys_name)
                        tables[sys_term] = CachedTable.hook(getattr(
                            h5file.root, sys_name))

                        weighted_events[sys_term] = rfile.Get(
                                sys_name + events_hist_suffix)[events_bin]

                        unused_terms.remove(sys_term)

                if systematics_samples:
                    for sample_name, sys_term in systematics_samples.items():

                        log.info("%s -> %s %s" % (name, sample_name, sys_term))

                        sys_term = tuple(sys_term.split(','))
                        sys_ds = self.db[sample_name]
                        sample_name = sample_name.replace('.', '_')
                        sample_name = sample_name.replace('-', '_')

                        trees[sys_term] = rfile.Get(sample_name)
                        tables[sys_term] = CachedTable.hook(getattr(
                            h5file.root, sample_name))

                        weighted_events[sys_term] = getattr(rfile,
                                sample_name + events_hist_suffix)[events_bin]

                        unused_terms.remove(sys_term)

                if unused_terms:
                    log.debug("UNUSED TERMS for %s:" % self.name)
                    log.debug(unused_terms)

                    for term in unused_terms:
                        trees[term] = None # flag to use NOMINAL
                        tables[term] = None
                        weighted_events[term] = None # flag to use NOMINAL

            if isinstance(self, Higgs):
                # use yellowhiggs for cross sections
                xs, _ = yellowhiggs.xsbr(
                        self.energy, self.masses[i],
                        Higgs.MODES_DICT[self.modes[i]][0], 'tautau')
                log.debug("{0} {1} {2} {3} {4} {5}".format(
                    name,
                    self.masses[i],
                    self.modes[i],
                    Higgs.MODES_DICT[self.modes[i]][0],
                    self.energy,
                    xs))
                xs *= TAUTAUHADHADBR
                kfact = 1.
                effic = 1.

            elif isinstance(self, Embedded_Ztautau):
                xs, kfact, effic = 1., 1., 1.

            else:
                xs, kfact, effic = ds.xsec_kfact_effic

            log.debug("{0} {1} {2} {3}".format(ds.name, xs, kfact, effic))
            self.datasets.append(
                    (ds, trees, tables, weighted_events, xs, kfact, effic))

    @property
    def label(self):

        l = self._label
        #if self.scale != 1. and not isinstance(self,
        #        (MC_Ztautau, Embedded_Ztautau)):
        #    l += r' ($\sigma_{SM} \times %g$)' % self.scale
        return l

    def draw_into(self, hist, expr, category, region,
                  cuts=None, weighted=True, systematics=True, scale=1.):

        if isinstance(expr, (list, tuple)):
            exprs = expr
        else:
            exprs = (expr,)

        if self.systematics and systematics:
            if hasattr(hist, 'systematics'):
                sys_hists = hist.systematics
            else:
                sys_hists = {}

        selection = self.cuts(category, region) & cuts

        for ds, sys_trees, sys_tables, sys_events, xs, kfact, effic in self.datasets:

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
                     self.get_weight_branches('NOMINAL', weighted=weighted))),
                 selection))

            log.debug(nominal_weighted_selection)

            current_hist = hist.Clone()
            current_hist.Reset()

            # fill nominal histogram
            for expr in exprs:
                nominal_tree.Draw(expr, nominal_weighted_selection,
                        hist=current_hist)

            hist += current_hist

            if not self.systematics or not systematics:
                continue

            # iterate over systematic variation trees
            for sys_term in sys_trees.iterkeys():

                # skip the nominal tree
                if sys_term == 'NOMINAL':
                    continue

                sys_hist = current_hist.Clone()

                sys_tree = sys_trees[sys_term]
                sys_event = sys_events[sys_term]

                if sys_tree is not None:

                    sys_hist.Reset()

                    sys_weight = (
                            LUMI[self.year] *
                            scale * self.scale *
                            xs * kfact * effic / sys_event)

                    sys_weighted_selection = (
                        '%f * %s * (%s)' %
                        (sys_weight,
                         ' * '.join(map(str,
                             self.get_weight_branches('NOMINAL',
                                 weighted=weighted))),
                         selection))

                    log.debug(sys_weighted_selection)

                    for expr in exprs:
                        sys_tree.Draw(expr, sys_weighted_selection, hist=sys_hist)

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

            # QCD + Ztautau fit error
            if isinstance(self, Ztautau):
                up_fit = current_hist.Clone()
                up_fit *= ((self.scale + self.scale_error) / self.scale)
                down_fit = current_hist.Clone()
                down_fit *= ((self.scale - self.scale_error) / self.scale)
                if ('ZFIT_UP',) not in sys_hists:
                    sys_hists[('ZFIT_UP',)] = up_fit
                    sys_hists[('ZFIT_DOWN',)] = down_fit
                else:
                    sys_hists[('ZFIT_UP',)] += up_fit
                    sys_hists[('ZFIT_DOWN',)] += down_fit
            else:
                for _term in [('ZFIT_UP',), ('ZFIT_DOWN',)]:
                    if _term not in sys_hists:
                        sys_hists[_term] = current_hist.Clone()
                    else:
                        sys_hists[_term] += current_hist.Clone()

            for _term in [('QCDFIT_UP',), ('QCDFIT_DOWN',)]:
                if _term not in sys_hists:
                    sys_hists[_term] = current_hist.Clone()
                else:
                    sys_hists[_term] += current_hist.Clone()

        if self.systematics and systematics:
            # set the systematics
            hist.systematics = sys_hists

    def draw_array(self, field_hist, category, region,
                   cuts=None, weighted=True,
                   field_scale=None,
                   weight_hist=None, weight_clf=None,
                   scores=None, systematics=True, scale=1.):

        do_systematics = self.systematics and systematics

        rec = self.merged_records(category, region,
                fields=field_hist.keys(), cuts=cuts,
                include_weight=True)

        if weight_hist is not None:
            clf_scores = self.scores(
                    weight_clf, category, region, cuts=cuts,
                    systematics=True)
            edges = np.array(list(weight_hist.xedges()))
            weights = np.array(weight_hist).take(edges.searchsorted(clf_scores['NOMINAL'][0]) - 1)
            weights = rec['weight'] * weights
        else:
            weights = rec['weight']

        if do_systematics:
            sys_hists = {}

        for field, hist in field_hist.items():
            if field_scale is not None and field in field_scale:
                arr = rec[field] * field_scale[field]
            else:
                arr = rec[field]
            if scores is not None:
                arr = np.c_[arr, scores['NOMINAL'][0]]
            hist.fill_array(arr, weights=weights * scale)
            if do_systematics:
                if not hasattr(hist, 'systematics'):
                    hist.systematics = {}
                sys_hists[field] = hist.systematics

        if not do_systematics:
            return

        for systematic in iter_systematics(False):

            rec = self.merged_records(category, region,
                    fields=field_hist.keys(), cuts=cuts,
                    include_weight=True,
                    systematic=systematic)

            if weight_hist is not None:
                edges = np.array(list(weight_hist.xedges()))
                weights = np.array(weight_hist).take(edges.searchsorted(clf_scores[systematic][0]) - 1)
                try:
                    weights = rec['weight'] * weights
                except:
                    log.warning("array lengths mismatch: %d, %d" %
                            (rec['weight'].shape[0], weights.shape[0]))
                    weights = rec['weight'] * np.concatenate([weights, [1.]])
            else:
                weights = rec['weight']

            for field, hist in field_hist.items():
                sys_hist = hist.Clone()
                sys_hist.Reset()
                if field_scale is not None and field in field_scale:
                    arr = rec[field] * field_scale[field]
                else:
                    arr = rec[field]
                if scores is not None:
                    arr = np.c_[arr, scores[systematic][0]]
                sys_hist.fill_array(arr, weights=weights * scale)
                if systematic not in sys_hists[field]:
                    sys_hists[field][systematic] = sys_hist
                else:
                    sys_hists[field][systematic] += sys_hist

    def scores(self, clf, category, region,
               cuts=None, scores_dict=None,
               systematics=True,
               scale=1.):

        # TODO check that weight systematics are included

        if scores_dict is None:
            scores_dict = {}

        for systematic in iter_systematics(True):

            if ((not systematics or not self.systematics)
                 and systematic != 'NOMINAL'):
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

        TEMPFILE.cd()
        selection = self.cuts(category, region) & cuts
        weight_branches = self.get_weight_branches(systematic)
        if systematic in SYSTEMATICS_BY_WEIGHT:
            systematic = 'NOMINAL'

        trees = []
        for ds, sys_trees, sys_tables, sys_events, xs, kfact, effic in self.datasets:

            if systematic in (('ZFIT_UP',), ('ZFIT_DOWN',),
                              ('QCDFIT_UP',), ('QCDFIT_DOWN',)):
                tree = sys_trees['NOMINAL']
                events = sys_events['NOMINAL']
            else:
                tree = sys_trees[systematic]
                events = sys_events[systematic]

                if tree is None:
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
                category,
                region,
                fields=None,
                cuts=None,
                include_weight=True,
                systematic='NOMINAL',
                scale=1.,
                **kwargs):

        if include_weight and fields is not None:
            if 'weight' not in fields:
                fields = fields + ['weight']

        selection = self.cuts(category, region, systematic) & cuts
        table_selection = selection.where()

        if systematic == 'NOMINAL':
            log.info("requesting table from %s" %
                     (self.__class__.__name__))
        else:
            log.info("requesting table from %s for systematic %s " %
                     (self.__class__.__name__, systematic))
        log.debug("using selection: %s" % selection)

        # TODO: handle cuts in weight expressions
        weight_branches = self.get_weight_branches(systematic, no_cuts=True)
        if systematic in SYSTEMATICS_BY_WEIGHT:
            systematic = 'NOMINAL'

        recs = []
        for ds, sys_trees, sys_tables, sys_events, xs, kfact, effic in self.datasets:

            if systematic in (('ZFIT_UP',), ('ZFIT_DOWN',),
                              ('QCDFIT_UP',), ('QCDFIT_DOWN',)):
                table = sys_tables['NOMINAL']
                events = sys_events['NOMINAL']
            else:
                table = sys_tables[systematic]
                events = sys_events[systematic]

                if table is None:
                    log.debug("systematics table was None, using NOMINAL")
                    table = sys_tables['NOMINAL']
                    events = sys_events['NOMINAL']

            actual_scale = self.scale
            if isinstance(self, Ztautau):
                if systematic == ('ZFIT_UP',):
                    actual_scale += self.scale_error
                elif systematic == ('ZFIT_DOWN',):
                    actual_scale -= self.scale_error

            weight = (
                    scale * actual_scale *
                    LUMI[self.year] *
                    xs * kfact * effic / events)

            # read the table with a selection
            rec = table.read_where(table_selection, **kwargs)

            # add weight field
            if include_weight:
                weights = np.empty(rec.shape[0], dtype='f4')
                weights.fill(weight)
                rec = recfunctions.rec_append_fields(rec,
                        names='weight',
                        data=weights,
                        dtypes='f4')
                # merge the weight fields
                rec['weight'] *= reduce(np.multiply,
                        [rec[br] for br in weight_branches])
                # drop other weight fields
                rec = recfunctions.rec_drop_fields(rec, weight_branches)
            if fields is not None:
                try:
                    rec = rec[fields]
                except:
                    print table
                    print rec.shape
                    print rec.dtype
                    raise
            recs.append(rec)
        return recs

    def indices(self,
                category,
                region,
                cuts=None,
                systematic='NOMINAL',
                **kwargs):

        selection = self.cuts(category, region, systematic) & cuts
        table_selection = selection.where()

        if systematic == 'NOMINAL':
            log.info("requesting indices from %s" %
                     (self.__class__.__name__))
        else:
            log.info("requesting indices from %s for systematic %s " %
                     (self.__class__.__name__, systematic))
        log.debug("using selection: %s" % selection)

        if systematic in SYSTEMATICS_BY_WEIGHT:
            systematic = 'NOMINAL'

        idxs = []
        for ds, sys_trees, sys_tables, sys_events, xs, kfact, effic in self.datasets:

            if systematic in (('ZFIT_UP',), ('ZFIT_DOWN',),
                              ('QCDFIT_UP',), ('QCDFIT_DOWN',)):
                table = sys_tables['NOMINAL']
                events = sys_events['NOMINAL']
            else:
                table = sys_tables[systematic]
                events = sys_events[systematic]

                if table is None:
                    log.debug("systematics table was None, using NOMINAL")
                    table = sys_tables['NOMINAL']
                    events = sys_events['NOMINAL']

            # read the table with a selection
            idx = table.get_where_list(table_selection, **kwargs)

            idxs.append(idx)
        return idxs

    def events(self, category, region,
               cuts=None,
               systematic='NOMINAL',
               weighted=True,
               scale=1.,
               raw=False):

        total = 0.
        hist = Hist(1, -100, 100)
        for ds, sys_trees, sys_tables, sys_events, xs, kfact, effic in self.datasets:
            tree = sys_trees[systematic]
            events = sys_events[systematic]
            if raw:
                selection = self.cuts(category, region, systematic=systematic) & cuts
                log.debug("requesing number of events from %s using cuts: %s"
                          % (tree.GetName(), selection))
                total += tree.GetEntries(selection)
            else:
                weight = LUMI[self.year] * self.scale * xs * kfact * effic / events
                weighted_selection = Cut(' * '.join(map(str,
                         self.get_weight_branches(systematic, weighted=weighted))))
                selection = Cut(str(weight)) * weighted_selection * (
                        self.cuts(category, region, systematic=systematic) & cuts)
                log.debug("requesing number of events from %s using cuts: %s"
                          % (tree.GetName(), selection))
                hist.Reset()
                curr_total = tree.Draw('1', selection, hist=hist)
                total += hist.Integral()
        return total * scale


class Ztautau(Background):
    pass


class MC_Ztautau(MC, Ztautau):

    SYSTEMATICS_COMPONENTS = MC.SYSTEMATICS_COMPONENTS + [
        'Z_FIT',
    ]

    def __init__(self, *args, **kwargs):
        """
        Instead of setting the k factor here
        the normalization is determined by a fit to the data
        """
        self.scale_error = 0.
        super(MC_Ztautau, self).__init__(
                *args, **kwargs)


class Embedded_Ztautau(MC, Ztautau):

    SYSTEMATICS_COMPONENTS = MC.SYSTEMATICS_COMPONENTS + [
        'Z_FIT',
    ]

    def __init__(self, *args, **kwargs):
        """
        Instead of setting the k factor here
        the normalization is determined by a fit to the data
        """
        self.scale_error = 0.
        super(Embedded_Ztautau, self).__init__(
                *args, **kwargs)


class EWK(MC, Background):

    pass


class Top(MC, Background):

    pass


class Diboson(MC, Background):

    pass


class Others(MC, Background):

    pass


class Higgs(MC, Signal):

    MASS_POINTS = range(100, 155, 5)

    MODES = ['Z', 'W', 'gg', 'VBF']

    MODES_DICT = {
        'gg': ('ggf', 'PowHegPythia_', 'PowHegPythia8_AU2CT10_'),
        'VBF': ('vbf', 'PowHegPythia_', 'PowHegPythia8_AU2CT10_'),
        'Z': ('zh', 'Pythia', 'Pythia8_AU2CTEQ6L1_'),
        'W': ('wh', 'Pythia', 'Pythia8_AU2CTEQ6L1_'),
    }

    # constant uncert term, high, low
    UNCERT_GGF = {
        'pdf_gg': (1.079, 0.923),
        'QCDscale_ggH1in': (1.133, 0.914),
    }

    UNCERT_VBF = {
        'pdf_qqbar': (1.027, 0.979),
        'QCDscale_qqH': (1.004, 0.996),
    }

    UNCERT_WZH = {
        'pdf_qqbar': (1.039, 0.961),
        'QCDscale_VH': (1.007, 0.992),
    }

    def histfactory(self, sample, systematics=True):
        if not systematics:
            return
        if len(self.modes) != 1:
            raise TypeError(
                    'histfactory sample only valid for single production mode')
        if len(self.masses) != 1:
            raise TypeError(
                    'histfactory sample only valid for single mass point')
        mode = self.modes[0]
        if mode == 'gg':
            overall_dict = self.UNCERT_GGF
        elif mode == 'VBF':
            overall_dict = self.UNCERT_VBF
        elif mode in ('Z', 'W'):
            overall_dict = self.UNCERT_WZH
        else:
            raise ValueError('mode %s is not valid' % mode)
        for term, (high, low) in overall_dict.items():
            log.info("defining overall sys %s" % term)
            sample.AddOverallSys(term, low, high)

    def __init__(self, year,
            mode=None, modes=None,
            mass=None, masses=None, **kwargs):

        if masses is None:
            if mass is not None:
                assert mass in Higgs.MASS_POINTS
                masses = [mass]
            else:
                masses = Higgs.MASS_POINTS
        else:
            assert len(masses) > 0
            for mass in masses:
                assert mass in Higgs.MASS_POINTS
            assert len(set(masses)) == len(masses)

        if modes is None:
            if mode is not None:
                assert mode in Higgs.MODES
                modes = [mode]
            else:
                modes = Higgs.MODES
        else:
            assert len(modes) > 0
            for mode in modes:
                assert mode in Higgs.MODES
            assert len(set(modes)) == len(modes)

        str_mass = ''
        if len(masses) == 1:
            str_mass = '(%d)' % masses[0]

        str_mode = ''
        if len(modes) == 1:
            str_mode = modes[0]
            self.name = 'Signal_%s' % modes[0]
        else:
            self.name = 'Signal'

        #self._label = r'%s$H%s\rightarrow\tau_{\mathrm{had}}\tau_{\mathrm{had}}$' % (
        #        str_mode, str_mass)
        self._label = r'%sH%s$\rightarrow\tau_{h}\tau_{h}$' % (str_mode, str_mass)
        if year == 2011:
            suffix = 'mc11c'
            generator_index = 1
        elif year == 2012:
            suffix = 'mc12a'
            generator_index = 2
        else:
            raise ValueError('No Higgs defined for year %d' % year)

        self.samples = []
        self.masses = []
        self.modes = []
        for mode in modes:
            generator = Higgs.MODES_DICT[mode][generator_index]
            for mass in masses:
                self.samples.append('%s%sH%d_tautauhh.%s' % (
                    generator, mode, mass, suffix))
                self.masses.append(mass)
                self.modes.append(mode)

        super(Higgs, self).__init__(year=year, **kwargs)

    @property
    def mode(self):

        if len(self.modes) != 1:
            raise RuntimeError(
                "Attempting to access mode for composite Higgs sample")
        return self.modes[0]


class QCD(Sample, Background):

    SYSTEMATICS_COMPONENTS = MC.SYSTEMATICS_COMPONENTS + [
        'QCD_FIT',
    ]

    @staticmethod
    def sample_compatibility(data, mc):

        if not isinstance(mc, (list, tuple)):
            raise TypeError("mc must be a list or tuple of MC samples")
        if not mc:
            raise ValueError("mc must contain at least one MC sample")
        systematics = mc[0].systematics
        for m in mc:
            if data.year != m.year:
                raise ValueError("MC and Data years do not match")
            if m.systematics != systematics:
                raise ValueError(
                    "two MC samples with inconsistent systematics setting")

    def __init__(self, data, mc,
                 scale=1.,
                 scale_error=0.,
                 data_scale=1.,
                 mc_scales=None,
                 shape_region='SS',
                 cuts=None,
                 color='#59d454'):

        QCD.sample_compatibility(data, mc)
        super(QCD, self).__init__(year=data.year, scale=scale, color=color)
        self.data = data
        self.mc = mc
        self.name = 'QCD'
        self.label = 'QCD Multi-jet (%s)' % shape_region.replace('_', ' ')
        self.scale = 1.
        self.data_scale = data_scale
        if mc_scales is not None:
            if len(mc_scales) != len(mc):
                raise ValueError("length of MC scales must match number of MC")
            self.mc_scales = mc_scales
        else:
            # default scales to 1.
            self.mc_scales = [1. for m in self.mc]
        self.scale_error = scale_error
        self.shape_region = shape_region
        self.systematics = mc[0].systematics

    def events(self, category, region, cuts=None,
               systematic='NOMINAL',
               raw=False):

        data = self.data.events(category, self.shape_region, cuts=cuts)
        mc_subtract = 0.
        for mc_scale, mc in zip(self.mc_scales, self.mc):
            mc_subtract += mc.events(
                category, self.shape_region,
                cuts=cuts,
                systematic=systematic,
                raw=raw,
                scale=mc_scale)

        log.info("QCD: Data(%.3f) - MC(%.3f)" % (data, mc_subtract))

        if raw:
            return self.data_scale * data + mc_subtract
        return (self.data_scale * data - mc_subtract) * self.scale

    def draw_into(self, hist, expr, category, region,
                  cuts=None, weighted=True, systematics=True):

        MC_bkg = hist.Clone()
        MC_bkg.Reset()
        for mc_scale, mc in zip(self.mc_scales, self.mc):
            mc.draw_into(MC_bkg, expr, category, self.shape_region,
                         cuts=cuts, weighted=weighted,
                         systematics=systematics, scale=mc_scale)

        data_hist = hist.Clone()
        data_hist.Reset()
        self.data.draw_into(data_hist, expr,
                            category, self.shape_region,
                            cuts=cuts, weighted=weighted)

        log.info("QCD: Data(%.3f) - MC(%.3f)" % (
            data_hist.Integral(),
            MC_bkg.Integral()))

        hist += (data_hist * self.data_scale - MC_bkg) * self.scale

        if hasattr(MC_bkg, 'systematics'):
            if not hasattr(hist, 'systematics'):
                hist.systematics = {}
            for sys_term, sys_hist in MC_bkg.systematics.items():
                scale = self.scale
                if sys_term == ('QCDFIT_UP',):
                    scale = self.scale + self.scale_error
                elif sys_term == ('QCDFIT_DOWN',):
                    scale = self.scale - self.scale_error
                qcd_hist = (data_hist * self.data_scale - sys_hist) * scale
                if sys_term not in hist.systematics:
                    hist.systematics[sys_term] = qcd_hist
                else:
                    hist.systematics[sys_term] += qcd_hist

        hist.SetTitle(self.label)

    def draw_array(self, field_hist, category, region,
                  cuts=None, weighted=True,
                  field_scale=None,
                  weight_hist=None, weight_clf=None,
                  scores=None, systematics=True):

        do_systematics = self.systematics and systematics

        field_hist_MC_bkg = dict([(expr, hist.Clone())
            for expr, hist in field_hist.items()])

        for mc_scale, mc in zip(self.mc_scales, self.mc):
            mc.draw_array(field_hist_MC_bkg, category, self.shape_region,
                         cuts=cuts, weighted=weighted,
                         field_scale=field_scale,
                         weight_hist=weight_hist, weight_clf=weight_clf,
                         scores=scores, systematics=systematics,
                         scale=mc_scale)

        field_hist_data = dict([(expr, hist.Clone())
            for expr, hist in field_hist.items()])

        self.data.draw_array(field_hist_data,
                            category, self.shape_region,
                            cuts=cuts, weighted=weighted,
                            field_scale=field_scale,
                            weight_hist=weight_hist, weight_clf=weight_clf,
                            scores=scores, systematics=systematics)

        for expr, h in field_hist.items():
            mc_h = field_hist_MC_bkg[expr]
            d_h = field_hist_data[expr]
            h += (d_h * self.data_scale - mc_h) * self.scale
            h.SetTitle(self.label)
            if not do_systematics:
                continue
            if hasattr(mc_h, 'systematics'):
                if not hasattr(h, 'systematics'):
                    h.systematics = {}
                for sys_term, sys_hist in mc_h.systematics.items():
                    scale = self.scale
                    if sys_term == ('QCDFIT_UP',):
                        scale = self.scale + self.scale_error
                    elif sys_term == ('QCDFIT_DOWN',):
                        scale = self.scale - self.scale_error
                    qcd_hist = (d_h * self.data_scale - sys_hist) * scale
                    if sys_term not in h.systematics:
                        h.systematics[sys_term] = qcd_hist
                    else:
                        h.systematics[sys_term] += qcd_hist

    def scores(self, clf, category, region,
               cuts=None, systematics=True,
               **kwargs):

        # SS data
        data_scores, data_weights = self.data.scores(
                clf,
                category,
                region=self.shape_region,
                cuts=cuts,
                **kwargs)

        scores_dict = {}
        # subtract SS MC
        for mc_scale, mc in zip(self.mc_scales, self.mc):
            mc.scores(
                    clf,
                    category,
                    region=self.shape_region,
                    cuts=cuts,
                    scores_dict=scores_dict,
                    systematics=systematics,
                    scale=mc_scale,
                    **kwargs)

        for sys_term in scores_dict.keys()[:]:
            sys_scores, sys_weights = scores_dict[sys_term]
            scale = self.scale
            if sys_term == ('QCDFIT_UP',):
                scale += self.scale_error
            elif sys_term == ('QCDFIT_DOWN',):
                scale -= self.scale_error
            # subtract SS MC
            sys_weights *= -1 * scale
            # add SS data
            sys_scores = np.concatenate((sys_scores, np.copy(data_scores)))
            sys_weights = np.concatenate((sys_weights, data_weights * scale))
            scores_dict[sys_term] = (sys_scores, sys_weights)

        return scores_dict

    def trees(self, category, region, cuts=None,
              systematic='NOMINAL'):

        TEMPFILE.cd()
        data_tree = asrootpy(
                self.data.data.CopyTree(
                    self.data.cuts(
                        category,
                        region=self.shape_region) & cuts))
        data_tree.userdata.weight_branches = []
        trees = [data_tree]
        for mc_scale, mc in zip(self.mc_scales, self.mc):
            _trees = mc.trees(
                    category,
                    region=self.shape_region,
                    cuts=cuts,
                    systematic=systematic,
                    scale=mc_scale)
            for tree in _trees:
                tree.Scale(-1)
            trees += _trees

        scale = self.scale
        if systematic == ('QCDFIT_UP',):
            scale += self.scale_error
        elif systematic == ('QCDFIT_DOWN',):
            scale -= self.scale_error

        for tree in trees:
            tree.Scale(scale)
        return trees

    def records(self,
                category,
                region,
                fields=None,
                cuts=None,
                include_weight=True,
                systematic='NOMINAL',
                **kwargs):

        assert include_weight == True

        data_records = self.data.records(
                category=category,
                region=self.shape_region,
                fields=fields,
                cuts=cuts,
                include_weight=include_weight,
                systematic='NOMINAL',
                **kwargs)
        arrays = data_records

        for mc_scale, mc in zip(self.mc_scales, self.mc):
            _arrays = mc.records(
                    category=category,
                    region=self.shape_region,
                    fields=fields,
                    cuts=cuts,
                    include_weight=include_weight,
                    systematic=systematic,
                    scale=mc_scale,
                    **kwargs)
            # FIX: weight may not be present if include_weight=False
            for array in _arrays:
                for partition in array:
                    partition['weight'] *= -1
            arrays.extend(_arrays)

        scale = self.scale
        if systematic == ('QCDFIT_UP',):
            scale += self.scale_error
        elif systematic == ('QCDFIT_DOWN',):
            scale -= self.scale_error

        # FIX: weight may not be present if include_weight=False
        for array in arrays:
            for partition in array:
                partition['weight'] *= scale
        return arrays

    def indices(self,
                category,
                region,
                cuts=None,
                systematic='NOMINAL',
                **kwargs):

        idxs = self.data.indices(
                category=category,
                region=self.shape_region,
                cuts=cuts,
                systematic='NOMINAL',
                **kwargs)

        for mc in self.mc:
            idxs.append(mc.indices(
                    category=category,
                    region=self.shape_region,
                    cuts=cuts,
                    systematic=systematic,
                    **kwargs))

        return idxs
