from __future__ import division
from textwrap import dedent
import colorsys
import numpy as np
from scipy import stats
import pandas as pd
import matplotlib as mpl
from matplotlib.collections import PatchCollection
import matplotlib.patches as Patches
import matplotlib.pyplot as plt
import warnings

from bokeh.io import show, output_file
from bokeh.plotting import figure



from .external.six import string_types
# from .external.six.moves import range

from . import utils
from .utils import iqr, categorical_order, remove_na
from .algorithms import bootstrap



__all__ = ["barplot"]


class _CategoricalPlotter(object):

    width = .8
    default_palette = "light"

    def establish_variables(self, x=None, y=None, hue=None, data=None,
                            orient=None, order=None, hue_order=None,
                            units=None):
        """Convert input specification into a common representation."""
        # Option 1:
        # We are plotting a wide-form dataset
        # -----------------------------------
        if x is None and y is None:

            # Do a sanity check on the inputs
            if hue is not None:
                error = "Cannot use `hue` without `x` or `y`"
                raise ValueError(error)

            # No hue grouping with wide inputs
            plot_hues = None
            hue_title = None
            hue_names = None

            # No statistical units with wide inputs
            plot_units = None

            # We also won't get a axes labels here
            value_label = None
            group_label = None

            # Option 1a:
            # The input data is a Pandas DataFrame
            # ------------------------------------

            if isinstance(data, pd.DataFrame):

                # Order the data correctly
                if order is None:
                    order = []
                    # Reduce to just numeric columns
                    for col in data:
                        try:
                            data[col].astype(np.float)
                            order.append(col)
                        except ValueError:
                            pass
                plot_data = data[order]
                group_names = order
                group_label = data.columns.name

                # Convert to a list of arrays, the common representation
                iter_data = plot_data.iteritems()
                plot_data = [np.asarray(s, np.float) for k, s in iter_data]

            # Option 1b:
            # The input data is an array or list
            # ----------------------------------

            else:

                # We can't reorder the data
                if order is not None:
                    error = "Input data must be a pandas object to reorder"
                    raise ValueError(error)

                # The input data is an array
                if hasattr(data, "shape"):
                    if len(data.shape) == 1:
                        if np.isscalar(data[0]):
                            plot_data = [data]
                        else:
                            plot_data = list(data)
                    elif len(data.shape) == 2:
                        nr, nc = data.shape
                        if nr == 1 or nc == 1:
                            plot_data = [data.ravel()]
                        else:
                            plot_data = [data[:, i] for i in range(nc)]
                    else:
                        error = ("Input `data` can have no "
                                 "more than 2 dimensions")
                        raise ValueError(error)

                # Check if `data` is None to let us bail out here (for testing)
                elif data is None:
                    plot_data = [[]]

                # The input data is a flat list
                elif np.isscalar(data[0]):
                    plot_data = [data]

                # The input data is a nested list
                # This will catch some things that might fail later
                # but exhaustive checks are hard
                else:
                    plot_data = data

                # Convert to a list of arrays, the common representation
                plot_data = [np.asarray(d, np.float) for d in plot_data]

                # The group names will just be numeric indices
                group_names = list(range((len(plot_data))))

            # Figure out the plotting orientation
            orient = "h" if str(orient).startswith("h") else "v"

        # Option 2:
        # We are plotting a long-form dataset
        # -----------------------------------

        else:

            # See if we need to get variables from `data`
            if data is not None:
                x = data.get(x, x)
                y = data.get(y, y)
                hue = data.get(hue, hue)
                units = data.get(units, units)

            # Validate the inputs
            for input in [x, y, hue, units]:
                if isinstance(input, string_types):
                    err = "Could not interpret input '{}'".format(input)
                    raise ValueError(err)

            # Figure out the plotting orientation
            orient = self.infer_orient(x, y, orient)

            # Option 2a:
            # We are plotting a single set of data
            # ------------------------------------
            if x is None or y is None:

                # Determine where the data are
                vals = y if x is None else x

                # Put them into the common representation
                plot_data = [np.asarray(vals)]

                # Get a label for the value axis
                if hasattr(vals, "name"):
                    value_label = vals.name
                else:
                    value_label = None

                # This plot will not have group labels or hue nesting
                groups = None
                group_label = None
                group_names = []
                plot_hues = None
                hue_names = None
                hue_title = None
                plot_units = None

            # Option 2b:
            # We are grouping the data values by another variable
            # ---------------------------------------------------
            else:

                # Determine which role each variable will play
                if orient == "v":
                    vals, groups = y, x
                else:
                    vals, groups = x, y

                # Get the categorical axis label
                group_label = None
                if hasattr(groups, "name"):
                    group_label = groups.name

                # Get the order on the categorical axis
                group_names = categorical_order(groups, order)

                # Group the numeric data
                plot_data, value_label = self._group_longform(vals, groups,
                                                              group_names)

                # Now handle the hue levels for nested ordering
                if hue is None:
                    plot_hues = None
                    hue_title = None
                    hue_names = None
                else:

                    # Get the order of the hue levels
                    hue_names = categorical_order(hue, hue_order)

                    # Group the hue data
                    plot_hues, hue_title = self._group_longform(hue, groups,
                                                                group_names)

                # Now handle the units for nested observations
                if units is None:
                    plot_units = None
                else:
                    plot_units, _ = self._group_longform(units, groups,
                                                         group_names)

        # Assign object attributes
        # ------------------------
        self.orient = orient
        self.plot_data = plot_data
        self.group_label = group_label
        self.value_label = value_label
        self.group_names = group_names
        self.plot_hues = plot_hues
        self.hue_title = hue_title
        self.hue_names = hue_names
        self.plot_units = plot_units

    def _group_longform(self, vals, grouper, order):
        """Group a long-form variable by another with correct order."""
        # Ensure that the groupby will work
        if not isinstance(vals, pd.Series):
            vals = pd.Series(vals)

        # Group the val data
        grouped_vals = vals.groupby(grouper)
        out_data = []
        for g in order:
            try:
                g_vals = np.asarray(grouped_vals.get_group(g))
            except KeyError:
                g_vals = np.array([])
            out_data.append(g_vals)

        # Get the vals axis label
        label = vals.name

        return out_data, label



    def infer_orient(self, x, y, orient=None):
        """Determine how the plot should be oriented based on the data."""
        orient = str(orient)

        def is_categorical(s):
            try:
                # Correct way, but does not exist in older Pandas
                try:
                    return pd.api.types.is_categorical_dtype(s)
                except AttributeError:
                    return pd.core.common.is_categorical_dtype(s)
            except AttributeError:
                # Also works, but feels hackier
                return str(s.dtype) == "categorical"

        def is_not_numeric(s):
            try:
                np.asarray(s, dtype=np.float)
            except ValueError:
                return True
            return False

        no_numeric = "Neither the `x` nor `y` variable appears to be numeric."

        if orient.startswith("v"):
            return "v"
        elif orient.startswith("h"):
            return "h"
        elif x is None:
            return "v"
        elif y is None:
            return "h"
        elif is_categorical(y):
            if is_categorical(x):
                raise ValueError(no_numeric)
            else:
                return "h"
        elif is_not_numeric(y):
            if is_not_numeric(x):
                raise ValueError(no_numeric)
            else:
                return "h"
        else:
            return "v"

    @property
    def hue_offsets(self):
        """A list of center positions for plots when hue nesting is used."""
        n_levels = len(self.hue_names)
        if self.dodge:
            each_width = self.width / n_levels
            offsets = np.linspace(0, self.width - each_width, n_levels)
            offsets -= offsets.mean()
        else:
            offsets = np.zeros(n_levels)

        return offsets

    @property
    def nested_width(self):
        """A float with the width of plot elements when hue nesting is used."""
        if self.dodge:
            width = self.width / len(self.hue_names) * .98
        else:
            width = self.width
        return width

    def annotate_axes(self, ax):
        """Add descriptive labels to an Axes object."""
        if self.orient == "v":
            xlabel, ylabel = self.group_label, self.value_label
        else:
            xlabel, ylabel = self.value_label, self.group_label

        if xlabel is not None:
            ax.set_xlabel(xlabel)
        if ylabel is not None:
            ax.set_ylabel(ylabel)

        if self.orient == "v":
            ax.set_xticks(np.arange(len(self.plot_data)))
            ax.set_xticklabels(self.group_names)
        else:
            ax.set_yticks(np.arange(len(self.plot_data)))
            ax.set_yticklabels(self.group_names)

        if self.orient == "v":
            ax.xaxis.grid(False)
            ax.set_xlim(-.5, len(self.plot_data) - .5, auto=None)
        else:
            ax.yaxis.grid(False)
            ax.set_ylim(-.5, len(self.plot_data) - .5, auto=None)

        if self.hue_names is not None:
            leg = ax.legend(loc="best")
            if self.hue_title is not None:
                leg.set_title(self.hue_title)

                # Set the title size a roundabout way to maintain
                # compatibility with matplotlib 1.1
                # TODO no longer needed
                try:
                    title_size = mpl.rcParams["axes.labelsize"] * .85
                except TypeError:  # labelsize is something like "large"
                    title_size = mpl.rcParams["axes.labelsize"]
                prop = mpl.font_manager.FontProperties(size=title_size)
                leg._legend_title_box._text.set_font_properties(prop)

    def add_legend_data(self, ax, color, label):
        """Add a dummy patch object so we can get legend data."""
        rect = plt.Rectangle([0, 0], 0, 0,
                             linewidth=self.linewidth / 2,
                             edgecolor=self.gray,
                             facecolor=color,
                             label=label)
        ax.add_patch(rect)




class _CategoricalStatPlotter(_CategoricalPlotter):

    @property
    def nested_width(self):
        """A float with the width of plot elements when hue nesting is used."""
        if self.dodge:
            width = self.width / len(self.hue_names)
        else:
            width = self.width
        return width

    def estimate_statistic(self, estimator, ci, n_boot):

        if self.hue_names is None:
            statistic = []
            confint = []
        else:
            statistic = [[] for _ in self.plot_data]
            confint = [[] for _ in self.plot_data]

        for i, group_data in enumerate(self.plot_data):

            # Option 1: we have a single layer of grouping
            # --------------------------------------------

            if self.plot_hues is None:

                if self.plot_units is None:
                    stat_data = remove_na(group_data)
                    unit_data = None
                else:
                    unit_data = self.plot_units[i]
                    have = pd.notnull(np.c_[group_data, unit_data]).all(axis=1)
                    stat_data = group_data[have]
                    unit_data = unit_data[have]

                # Estimate a statistic from the vector of data
                if not stat_data.size:
                    statistic.append(np.nan)
                else:
                    statistic.append(estimator(stat_data))

                # Get a confidence interval for this estimate
                if ci is not None:

                    if stat_data.size < 2:
                        confint.append([np.nan, np.nan])
                        continue

                    if ci == "sd":

                        estimate = estimator(stat_data)
                        sd = np.std(stat_data)
                        confint.append((estimate - sd, estimate + sd))

                    else:

                        boots = bootstrap(stat_data, func=estimator,
                                          n_boot=n_boot,
                                          units=unit_data)
                        confint.append(utils.ci(boots, ci))

            # Option 2: we are grouping by a hue layer
            # ----------------------------------------

            else:
                for j, hue_level in enumerate(self.hue_names):

                    if not self.plot_hues[i].size:
                        statistic[i].append(np.nan)
                        if ci is not None:
                            confint[i].append((np.nan, np.nan))
                        continue

                    hue_mask = self.plot_hues[i] == hue_level
                    if self.plot_units is None:
                        stat_data = remove_na(group_data[hue_mask])
                        unit_data = None
                    else:
                        group_units = self.plot_units[i]
                        have = pd.notnull(
                            np.c_[group_data, group_units]
                            ).all(axis=1)
                        stat_data = group_data[hue_mask & have]
                        unit_data = group_units[hue_mask & have]

                    # Estimate a statistic from the vector of data
                    if not stat_data.size:
                        statistic[i].append(np.nan)
                    else:
                        statistic[i].append(estimator(stat_data))

                    # Get a confidence interval for this estimate
                    if ci is not None:

                        if stat_data.size < 2:
                            confint[i].append([np.nan, np.nan])
                            continue

                        if ci == "sd":

                            estimate = estimator(stat_data)
                            sd = np.std(stat_data)
                            confint[i].append((estimate - sd, estimate + sd))

                        else:

                            boots = bootstrap(stat_data, func=estimator,
                                              n_boot=n_boot,
                                              units=unit_data)
                            confint[i].append(utils.ci(boots, ci))

        # Save the resulting values for plotting
        self.statistic = np.array(statistic)
        self.confint = np.array(confint)

    def draw_confints(self, ax, at_group, confint, colors,
                      errwidth=None, capsize=None, **kws):

        if errwidth is not None:
            kws.setdefault("lw", errwidth)
        else:
            kws.setdefault("lw", mpl.rcParams["lines.linewidth"] * 1.8)

        for at, (ci_low, ci_high), color in zip(at_group,
                                                confint,
                                                colors):
            if self.orient == "v":
                ax.plot([at, at], [ci_low, ci_high], color=color, **kws)
                if capsize is not None:
                    ax.plot([at - capsize / 2, at + capsize / 2],
                            [ci_low, ci_low], color=color, **kws)
                    ax.plot([at - capsize / 2, at + capsize / 2],
                            [ci_high, ci_high], color=color, **kws)
            else:
                ax.plot([ci_low, ci_high], [at, at], color=color, **kws)
                if capsize is not None:
                    ax.plot([ci_low, ci_low],
                            [at - capsize / 2, at + capsize / 2],
                            color=color, **kws)
                    ax.plot([ci_high, ci_high],
                            [at - capsize / 2, at + capsize / 2],
                            color=color, **kws)


class _BarPlotter(_CategoricalStatPlotter):
    """Show point estimates and confidence intervals with bars."""

    def __init__(self, x, y, hue, data, order, hue_order,
                 estimator, ci, n_boot, units,
                 orient, color, palette, saturation, errcolor,
                 errwidth, capsize, dodge):
        """Initialize the plotter."""
        self.establish_variables(x, y, hue, data, orient,
                                 order, hue_order, units)
        # self.establish_colors(color, palette, saturation)
        self.estimate_statistic(estimator, ci, n_boot)

        self.dodge = dodge

        self.errcolor = errcolor
        self.errwidth = errwidth
        self.capsize = capsize

    def draw_bars(self):
        """Draw the bars onto `ax`."""
        # Get the right matplotlib function depending on the orientation
        # barfunc = ax.bar if self.orient == "v" else ax.barh
        barpos = np.arange(len(self.statistic))

        barpos = [str(a) for a in barpos]

        print(barpos)
        print(self.statistic)



        if self.plot_hues is None:

            p = figure(x_range=self.group_names, plot_height=250)

            p.vbar(x=self.group_names, top=self.statistic, width=0.7)

            show(p)


            #
            # # Draw the bars
            # barfunc(barpos, self.statistic, self.width,
            #         color=self.colors, align="center", **kws)
            #
            # # Draw the confidence intervals
            # errcolors = [self.errcolor] * len(barpos)
            # self.draw_confints(ax,
            #                    barpos,
            #                    self.confint,
            #                    errcolors,
            #                    self.errwidth,
            #                    self.capsize)

        else:

            for j, hue_level in enumerate(self.hue_names):

                # Draw the bars
                offpos = barpos + self.hue_offsets[j]
                barfunc(offpos, self.statistic[:, j], self.nested_width,
                        color=self.colors[j], align="center",
                        label=hue_level, **kws)

                # Draw the confidence intervals
                if self.confint.size:
                    confint = self.confint[:, j]
                    errcolors = [self.errcolor] * len(offpos)
                    self.draw_confints(ax,
                                       offpos,
                                       confint,
                                       errcolors,
                                       self.errwidth,
                                       self.capsize)

    def plot(self):
        """Make the plot."""
        self.draw_bars()
        #
        # self.annotate_axes(ax)
        # if self.orient == "h":
        #     ax.invert_yaxis()




def barplot(x=None, y=None, hue=None, data=None, order=None, hue_order=None,
            estimator=np.mean, ci=95, n_boot=1000, units=None,
            orient=None, color=None, palette=None, saturation=.75,
            errcolor=".26", errwidth=None, capsize=None, dodge=True,
            ax=None, **kwargs):

    plotter = _BarPlotter(x, y, hue, data, order, hue_order,
                          estimator, ci, n_boot, units,
                          orient, color, palette, saturation,
                          errcolor, errwidth, capsize, dodge)

    # if ax is None:
    #     ax = plt.gca()

    plotter.plot()
    return ax
