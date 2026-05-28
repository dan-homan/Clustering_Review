#!/usr/bin/env python
# coding: utf-8

#-------------------------------------------------------------------
# find_clusters.py    by Dan Homan,  homand@denison.edu
#
# A python script for finding clusters of points in an image and modeling
# a series of images over time.  The default uses clean components, but these
# procedures could be adpated to use pixels or other point data.
#
# Usage: python find_clusters.py -h  to see options.
#

VERS='2026_05_07'

#-------------------------------------------------------------------
# Change Log
#
# 2025-06-19:
#  - Initial version created. Changes from this point are documented in
#    cluster_code_changelog.txt
# 

#-------------------------------------------------------------------
# Import Libraries Needed
#

import os
import numpy as np
#import scipy 
import matplotlib
# check if a matplotlib backend is defined
#. --> if not use Qt5Agg which seems to behave well 
if 'MPLBACKEND' not in os.environ:
    matplotlib.use('Qt5Agg')
#
import matplotlib.pyplot as plt
from matplotlib import cm
from matplotlib.animation import FuncAnimation
import pandas as pd
import glob
import shutil
import sys
import json
import argparse


import cluster_code as cl

#
CCVERS = cl.setup_cluster_code()

#
# Set the "MOJAVE_DATA" environment variable to define data directory
#   
if 'MOJAVE_DATA' not in os.environ:
    print("MOJAVE_DATA environment variable not set., Trying ROOT_DATA...")
    if 'ROOT_DATA' in os.environ:
        root_data_dir = os.environ['ROOT_DATA']
        print("Using ROOT_DATA environment variable for data path:", root_data_dir)
    else:
        # Default path if neither environment variable is set
        # Note: Change this to your own default path as needed
        root_data_dir = "/Users/homand/Dropbox/Research/MOJAVE_data/MOJAVE_Data_Shared/"
        print("Warning: MOJAVE_DATA environment variable not set, using default path.", root_data_dir)
else:
    root_data_dir = os.environ['MOJAVE_DATA']

#-------------------------------------------------------------------------------
#
# Function to save summary plots to a PDF file
#
def save_summary_plots(epoch_info, cc_data, root_data_dir, cl_df, cc_labels, 
                       file_prefix="", colorImages=False):
    """
    Save summary plots to a PDF file.
    
    Parameters:
    epoch_info : DataFrame
        Information about each epoch.
    cc_data : DataFrame
        Clean component data.
    root_data_dir : str
        Root directory for data files.
    cl_df : DataFrame
        Cluster data frame.
    cc_labels : array-like
        Labels for clean components.
    """

    from matplotlib.backends.backend_pdf import PdfPages

    #
    # Define plotting limits based on cluster positions and sizes
    #
    # get max, min positions relative to the core for defining plotting area
    #  --> include cluster size estimates 
    xpos = cl_df['avg_x']-cl_df['core_x']
    ypos = cl_df['avg_y']-cl_df['core_y']
    #xpos = cluster_epoch_df['centX']
    #ypos = cluster_epoch_df['centY']
    median_beam = np.nanmedian(epoch_info['bmaj'])
    xmin = np.min(xpos - 2*cl_df['sizeMaj']) - 1.5*median_beam
    xmax = np.max(xpos + 2*cl_df['sizeMaj']) + 1.5*median_beam
    ymin = np.min(ypos - 2*cl_df['sizeMaj']) - 1.5*median_beam
    ymax = np.max(ypos + 2*cl_df['sizeMaj']) + 1.5*median_beam
    xspan = xmax-xmin
    yspan = ymax-ymin
    xrange = [ xmin - 0.05*xspan , xmax + 0.05*xspan ]
    yrange = [ ymin - 0.05*yspan , ymax + 0.05*yspan ]

    lims=[xrange[1],xrange[0],yrange[0],yrange[1]]

    # compute shifts for images and clean components
    if xspan < yspan:
        xshift = 0.7*xspan
        yshift = 0
    else:
        yshift = -0.7*yspan
        xshift = 0          
    
    # add a 'select' column to cl_df, expected for interactive plotting 
    cl_df['select'] = False

    #
    # make summary plots
    #
    filename = file_prefix + 'summary_plots.pdf'
    pp = PdfPages(file_prefix + 'summary_plots.pdf')
    #
    fig1,ax1=cl.make_summary_plots(None, None, cl_df, 
                                flux_threshold=0.0, alternate_plots="Tb",
                                xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
    #
    # create separate figure for legend
    #
    handles, labels = ax1[0,0].get_legend_handles_labels()
    fig_leg, ax_leg = plt.subplots(figsize=(8,10))
    ax_leg.legend(handles, labels, loc='upper center', ncol=4, fontsize=12)
    ax_leg.axis('off')
    fig1.legends = []  # remove legends from fig1 to avoid duplication in pdf
    #
    # Create a plot of clean components used in the analysis for quick reference
    #
    fig_cc, ax_cc, _ = cl.plot_cclist(cc_data, make_square=True, color_epochs=True, show_plot=False)
    pp.savefig(fig1)
    pp.savefig(fig_leg)
    pp.savefig(fig_cc)
    pp.close()
    plt.close(fig1)
    plt.close(fig_leg)
    plt.close(fig_cc)   
    print("Saved summary plots to {0}".format(filename))
    #return
    #
    # make overplot plots for each epoch into a movie
    #
    filename = file_prefix + 'epoch_overplots.mp4'
    plt.rcParams["figure.figsize"] = (8, 8)
    fig2, ax2 = plt.subplots()
    dummy_image = ax2.imshow(np.array([[0, 1]]), cmap='viridis', visible=False)
    cb2 = fig2.colorbar(dummy_image, pad=0.01) 
    def make_frame(i):
        ax2.clear()
        if colorImages:
            cmap = cm.cubehelix_r
            ptype = 'Color'
        else:
            cmap = None
            ptype = 'Contour'
        cl.overplot_clusters(epoch_info, cc_data, root_data_dir,
                          cl_df, cc_labels, i,
                          xshift=xshift, yshift=yshift, lims=lims, fig=fig2, ax=ax2, cb=cb2,
                          cmap=cmap, ptype=ptype)
        #ax.set_title(f"Epoch {i+1}/{epoch_info.shape[0]}")
        return ax2
    ani = FuncAnimation(fig2, make_frame, frames=epoch_info.shape[0], blit=False, interval=500)
    ani.save(filename, writer='ffmpeg', fps=1)
    plt.close(fig2)
    print("Saved epoch overplot animation to {0}".format(filename))
   
    return

#
# Only execute the following code if run as a script
#
if __name__ == "__main__":
    #-------------------------------------------------------------------
    # Define the arguments used by this script
    #
    parser = argparse.ArgumentParser(description="A script to find clusters of points in an image and model a series of images over time.")
    parser.add_argument("source", help="Source name, can serve as base for uvfile name and output files")
    parser.add_argument("--band", help="Band [u,k,q], default=u", default="u")
    parser.add_argument("--root_data_dir", help="root data directory, default=MOJAVE_DATA environment variable", 
                        default=root_data_dir)
    parser.add_argument("--min_clusters", type=int, help="Minimum number of clusters to find, default=1", 
                        default=1)
    parser.add_argument("--max_clusters", type=int, help="Maximum number of clusters to find, default=12", 
                        default=16)
    parser.add_argument("--complex", type=float, help="Complexity: float value that sets the balance between number of parameters and fit quality when calculating bic*.  Larger values = more clusters favored, default=3.0", 
                        default=3.0)
    parser.add_argument("--min_epoch", help="minimum epoch to include in analysis, default=1994.0", 
                        type=float, default=1994.0)
    parser.add_argument("--max_epoch", help="maximum epoch to include in analysis, default=2200.0", 
                        type=float, default=2200.0)
    parser.add_argument("--method", help="Method to use for clustering, default=KMeans",    
                        default="KMeans", choices=["KMeans","GMM"])
    parser.add_argument("--SigmaCut", type=float, help="Distance from cluster center in standard deviations at which to flag a clean component default=0 --> don't flag any", 
                        default=0.0)
    parser.add_argument("--coreIDMethod", help="Method to use for core identification, default=JetEnd",    
                        default="JetEnd", choices=["JetEnd","Center"])
    parser.add_argument("--JetDir", help="Provide Jet Dir on sky to help coreID, default=None",    
                        type=float, default=None)
    parser.add_argument("--include_QU", help="Include QU data in analysis, default=True", 
                        type=bool, default=True)
    parser.add_argument("--StokesQU_weight", help="Weight for Stokes Q and U data in clustering, default=1e-9", 
                        type=float, default=1e-9)
    parser.add_argument("--results_dir", help="Directory for results files, default=.", 
                        default=None)
    parser.add_argument("--ccfile_dir", help="Directory for resulting cc files, default=./full_ccfiles", 
                        default="./full_ccfiles")
    parser.add_argument("--window_Nepochs", type=int, help="Number of epochs to use for windowing, default=9. An integer value > 0 will use that fixed number of epochs per window, while <= 0 will use all epochs.", 
                        default=9)
    parser.add_argument("--maxGap", type=float, help="Maximum gap between epochs in years when windowing, default=2.0", 
                        default=2.0)
    parser.add_argument("--show_results", help="Show plots of results", action='store_true', default=False)
    parser.add_argument("--print_diag", help="Print diagnostic information during processing", action='store_true', default=False)
    parser.add_argument("--editN", help="Interactive editing of N estimates in each window of time", action='store_true', default=False)
    parser.add_argument("--editCC", help="Force interactive editing of CC data", action='store_true', default=False)
    parser.add_argument("--EGauss", help="Use Elliptical Gaussians where possible, default=True", default="True", type=str)
    parser.add_argument("--flux_threshold", help="Lower limit on tracking, plotting cluster properties, default=0.0", default=0.0,type=float)
    parser.add_argument("--cpus", help="Number of cpus to use, default=1", default=1, type=int)
    parser.add_argument("--mySQL_ID", help="Provide user ID to save results in mySQL, default=None", default=None)
    parser.add_argument("--flux_match", help="Maximum log10 difference in flux for matching clusters at the same epoch across windows, default=0.15", default=0.15,type=float)
    parser.add_argument("--pos_match", help="Maximum fractional position difference (as a fraction of fwhm size) for matching clusters, at the same epoch across windows, default=0.1", default=0.1,type=float)    
    parser.add_argument("--area_match", help="Maximum log10 difference in cluster area for matching clusters at the same epoch across windows, default=0.3", default=0.3,type=float)
    parser.add_argument("--dont_confirm", help="Do not ask user to confirm mySQL changes", action='store_true', default=False)
    parser.add_argument("--recalc_all", help="Recalculate everything, don't refer to previous results", action='store_true', default=False)
    parser.add_argument("--recalc_fits", help="Recalculate cluster fits to all windows (not just those with changes). Keep Ncluster values, cross-IDs, and other flags if possible from previous results", action='store_true', default=False)
    parser.add_argument("--recalc_N", help="Recalculate all Ncluster values, cross-IDs, and reset other flags, don't refer to previous results", action='store_true', default=False)
    parser.add_argument("--recalc_IDs", help="Recalculate cross-IDs, don't refer to previous results for cluster labels", action='store_true', default=False)
    parser.add_argument("--refresh_data", help="Load fresh data from --ccfile_dir, don't use saved cc data from previous run", action='store_true', default=False)
    parser.add_argument("--colorImages", help="Color images by epoch in overplots, default=False", action='store_true', default=False)
    parser.add_argument("--set_core", help="Open interactive setting of core position in each epoch.  This will also force --coreIDMethod to be \'Center\'",  action='store_true', default=False)

    args = parser.parse_args()
    config = vars(args)
    if config["EGauss"] in ['False','false','0']:
        config["EGauss"] = False
    elif config["EGauss"] in ['True','true','1']:
        config["EGauss"] = True
    else:      
        print("EGauss must be True or False, not {0}".format(config["EGauss"]))
        sys.exit(1) 
    
    print("--------------------------------------------------")
    print("find_clusters.py version: {0}".format(VERS)) 
    print("cluster_code.py version: {0}".format(CCVERS))   
    print("--------------------------------------------------")
    print("Finding Clusters with the following configuration:")
    print("--------------------------------------------------")
    print(pd.DataFrame(config.items(),columns=['Variable','Value']))
    print("--------------------------------------------------")

    # Save the current run string and create a list of strings as a history
    #.  of changes made during this run.  This run_history list will be
    #.  passed to functions that might make changes to previous results 
    current_run_string = "python "+ " ".join(sys.argv) + "\n"
    print("Current run command:\n{0}".format(current_run_string))

    run_history = []
    run_history.append("#--------------------------------------------------\n")
    run_history.append(current_run_string)
    run_history.append("# find_clusters.py run on {0}\n".format(pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")))
    run_history.append("# find_clusters.py version: {0}\n".format(VERS))
    run_history.append("# cluster_code.py version: {0}\n".format(CCVERS))

    #-------------------------------------------------------------------
    # Run the clustering code
    #

    #-------------------------------------------------------------------------------
    #
    # Define quantities for this run
    #
    #--------------------------------------------------------------------------------
    source = config["source"]  # Source name, e.g., "3C273"
    #
    band = config["band"]  # Band, e.g., "u", "k", "q"
    if band not in ['u','q','k']:
        if band in ['U','K','Q']:
            band = band.lower()  # Ensure band is lowercase
            config["band"] = band
        else:
            print("Band must be one of 'u', 'k', or 'q'.")
            sys.exit(1)
    #
    root_data_dir = config["root_data_dir"]  # Root data directory
    if not root_data_dir.endswith('/'):      # Ensure it ends with a '/'    
        root_data_dir += '/'

    complex = config["complex"]  # Complexity factor for cluster estimation
    #    
    min_clusters = config[ "min_clusters" ]  # Minimum number of clusters to find 
    max_clusters = config[ "max_clusters" ]  # Maximum number of clusters to find
    # 
    min_epoch = config["min_epoch"]  # Minimum epoch to include in analysis
    max_epoch = config["max_epoch"]  # Maximum epoch to include in analysis
    #
    Method = config["method"]  # Method to use for clustering, e.g., "KMeans" or "GMM"
    CoreIDMethod= config["coreIDMethod"]  # Method to use for core identification, e.g., "JetEnd" or "Center"
    include_QU= config["include_QU"]  # Include QU data in analysis
    StokesQU_weight = config["StokesQU_weight"]  # Weight for Stokes Q and U data in clustering
    if not(include_QU):
        StokesQU_weight = 0.0
        config["StokesQU_weight"] = StokesQU_weight  # Update config with new value

    #
    # setup filenames, results directory, configuration file, etc...
    #
    results_dir = config["results_dir"]  # Directory for results files
    if results_dir is None: 
        results_dir = './'
    elif not results_dir.endswith('/'):
        results_dir += '/'
    results_dir += "{0}{1}_{2:.2f}-{3:.2f}/".format(source,band,min_epoch,max_epoch)
    if not os.path.exists(results_dir):
        os.makedirs(results_dir)
        print(f"Directory '{results_dir}' created.")
    #
    config_file = results_dir + "config.json"
    if config['window_Nepochs'] > 0:
        windowing = True
        config_file = results_dir + "config_win.json"
    else:
        windowing = False   
    #
    # Check if configuration file exists and load it if so
    #
    save_config = True
    ReCalculate_all = config['recalc_all']
    ReCalculate_fits = (ReCalculate_all or config['recalc_fits'])
    ReCalculate_Nwin = (ReCalculate_all or config['recalc_N'])
    ReCalculate_crossIDs = (ReCalculate_Nwin or config['recalc_IDs'])
    #
    if os.path.exists(config_file):  # disabled for now
        print("--------------------------------------------------")
        print("Previous configuration file exists!\nLoading {0} ...".format(config_file))
        with open(config_file, 'r') as f:
            load_config = json.load(f)
        #print(pd.DataFrame(load_config.items(),columns=['Variable','Value']))
        #print("--------------------------------------------------")
        #
        # Check if the loaded configuration matches the command line arguments
        if complex != load_config["complex"]:
            print("Warning: Complexity factor in config file ({0}) does not match command line ({1}).".format(load_config["complex"], complex))
            print("Using command line value for complex parameter + recalculating Nwin.")
            save_config = True
            ReCalculate_Nwin = True
            ReCalculate_crossIDs = True
        if config['pos_match'] != load_config["pos_match"]:
            print("Warning: pos_match factor in config file ({0}) does not match command line ({1}).".format(load_config["pos_match"], config['pos_match']))
            print("Using command line value for pos_match parameter + recalculating cross-IDs.")
            save_config = True
            ReCalculate_crossIDs = True
        if config['flux_match'] != load_config["flux_match"]:
            print("Warning: flux_match factor in config file ({0}) does not match command line ({1}).".format(load_config["flux_match"], config['flux_match']))
            print("Using command line value for flux_match parameter + recalculating cross-IDs.")
            save_config = True
            ReCalculate_crossIDs = True
        if config['area_match'] != load_config["area_match"]:
            print("Warning: area_match factor in config file ({0}) does not match command line ({1}).".format(load_config["area_match"], config['area_match']))
            print("Using command line value for area_match parameter + recalculating cross-IDs.")
            save_config = True
            ReCalculate_crossIDs = True
        if root_data_dir != load_config["root_data_dir"]:
            print("Note: Root data directory in config file does not match command line:\n {0}\n {1}".format(load_config["root_data_dir"], root_data_dir))
            print("Using command line value for root data directory.")
            save_config = True
        if "mySQL_ID" in load_config and config["mySQL_ID"] != load_config["mySQL_ID"]:
            print("Note: mySQL_ID flag in config file ({0}) does not match command line ({1}).".format(load_config["mySQL_ID"], config["mySQL_ID"]))
        print("--------------------------------------------------") 
        #
        # check for other critical parameters that must match to prevent recalculation
        #
        if (load_config["min_clusters"] != min_clusters or
            load_config["max_clusters"] != max_clusters or
            load_config["maxGap"] != config['maxGap'] or
            load_config["method"] != Method or
            load_config["coreIDMethod"] != CoreIDMethod or
            load_config["include_QU"] != include_QU or
            load_config["EGauss"] != config['EGauss'] or
            load_config["SigmaCut"] != config['SigmaCut'] or
            load_config["StokesQU_weight"] != StokesQU_weight or
            load_config["window_Nepochs"] != config['window_Nepochs'] or
            load_config["JetDir"] != config['JetDir']):
            #
            print("Warning: previous configuration file does not match important command line arguments.")
            resp = input("Do you want to recalculate all results with the command line arguments? (y/n): ").strip().lower()
            if resp == 'y':
                ReCalculate_all = True
                ReCalculate_fits = True
                ReCalculate_Nwin = True
                ReCalculate_crossIDs = True
                save_config = True
            else:
                exit(0)
    else:
        save_config = True

    #
    # update history list if any recalculations are needed based on config file comparison
    # 
    if ReCalculate_all:
        run_history.append("# Recalculated all results based on command line arguments differing from config file.\n")
    elif ReCalculate_fits:
        run_history.append("# Recalculated all window fits based on command line arguments differing from config file, but kept Nwin and cross-ID results if possible.\n")
    if ReCalculate_Nwin and not ReCalculate_all:
        run_history.append("# Recalculated Nwin values based on command line arguments differing from config file.\n")    
    if ReCalculate_crossIDs and not ReCalculate_all:
        run_history.append("# Recalculated cross-ID results based on command line arguments differing from config file.\n")       

    #
    save_name = results_dir + source + band
    save_cc_name = results_dir + source + band + ".edit.i_cc"
    if include_QU:
        save_cc_name = save_cc_name.replace(".i_cc",".iqu_cc")
    
    save_cc_name = save_cc_name + ".{0:.2f}-{1:.2f}".format(min_epoch,max_epoch)

    #-------------------------------------------------------------------------------
    #
    # Obtain and edit clean component list if needed
    #
    #--------------------------------------------------------------------------------
    #
    # Do we need to process clean components for this case?
    #
    save_cc_file = False
    edit_cclist = config['editCC']
    core_pos = None  # apriori core position estimates
    #
    # First check if we have already edited and saved the ccdata for this source
    if os.path.exists(save_cc_name+".npz") and not(config['refresh_data']):
        print("Edited CC file exisits!\nLoading {0} ...".format(save_cc_name+".npz"))
        cc_npzfile = np.load(save_cc_name+".npz",allow_pickle=True)
        full_cc_filename = cc_npzfile['full_cc_file']
        epoch_info = cc_npzfile['epoch_info']
        ccdata = cc_npzfile['ccdata']
        # Load core positions if they were previously estimated and saved in file
        try:
            core_pos = cc_npzfile['core_pos']
        except:
            core_pos = None
            print("No core_pos found in ccfile; core positions will be determined automatically") 
    #
    # If not, we need to load a full ccfile and slice it to the epochs we want
    else:
        # check for a Full CC file for this source we can edit...
        ccfile_dir = config["ccfile_dir"]  # Directory for full ccfiles files
        if ccfile_dir is None: 
            ccfile_dir = './'
        elif not ccfile_dir.endswith('/'):
            ccfile_dir += '/'
        #
        full_cc_name = ccfile_dir + source + band + ".full.i_cc"
        edit_cc_name = ccfile_dir + source + band + ".edit.i_cc"
        if include_QU:
            full_cc_name = full_cc_name.replace(".i_cc",".iqu_cc")
            edit_cc_name = edit_cc_name.replace(".i_cc",".iqu_cc")

        filematches = glob.glob(edit_cc_name+"*.npz")
        if len(filematches) == 0:
            print("No full, edited CC files matching pattern: {0}".format(edit_cc_name+"*.npz"))
            print("Checking for full, unedited CC files to edit...")
            #
            # If no edited file exists, check for a full, unedited CC file to edit
            #
            filematches = glob.glob(full_cc_name+"*.npz")
            edit_cclist = True
        #
        if len(filematches) > 0:
            if len(filematches) == 1:
                full_cc_filename = filematches[0]
                print("Loading {0} ...".format(full_cc_filename))
                cc_npzfile = np.load(full_cc_filename,allow_pickle=True)
                if edit_cclist:
                    full_epoch_info = cc_npzfile['full_ep_info']
                    full_ccdata = cc_npzfile['full_data']
                else:
                    full_epoch_info = cc_npzfile['epoch_info']
                    full_ccdata = cc_npzfile['ccdata']
            else:
                print("Multiple matching Full CC files:", filematches)
                exit(0)
        else:
            print("No CC files matching pattern: {0}".format(full_cc_name+"*.npz"))
            exit(0)
        
        #
        # Select epochs to include in analysis, drop flagged cc points as well
        #
        ccdata, epoch_info, _ = cl.select_epoch_range(full_ccdata, full_epoch_info, 
                                                   min_epoch, max_epoch, show_info=True,
                                                   run_history=run_history)
        #
        save_cc_file = True

    #
    # Visually edit the ccdata
    #
    if edit_cclist:
        ccdata, points_flagged = cl.edit_ccdata(ccdata,run_history=run_history)
        if points_flagged:
            save_cc_file = True
        elif not(save_cc_file):
            print("No points interactive flagged, not saving an update.")

    #
    # Setting core positions estimates (if requested) before clustering
    #.  --> these will be saved in the ccfile moving forward so they are available 
    #       on reruns
    #
    if config['set_core']:
        print("-------------------------------------------------------------------------------------------")
        print("Using set core positions as a first estimate for fitting, setting coreIDMethod = \'Center\'")
        config['coreIDMethod'] = "Center"
        if core_pos is None:
            print("No core_pos file found, using interactive mode...")
            core_pos = cl.Interactive_Core_Selection(ccdata, epoch_info, root_data_dir)
            run_history.append("# Used interactive mode to set core position estimates, used coreIDMethod = \'Center\'.\n")       
        else:
            print("Using core position estimates previously saved in {0}".format(save_cc_name))
            run_history.append("# Used core position estimates previously saved in {0}, used coreIDMethod = \'Center\'.\n".format(save_cc_name))       
        print(core_pos)
        save_cc_file = True
        print("-------------------------------------------------------------------------------------------")

    #
    # Save the results so we don't have to edit or slice epochs again...
    #
    if save_cc_file:
        if core_pos is None:
            np.savez_compressed(save_cc_name,
                    full_cc_file=full_cc_filename,
                    ccdata=ccdata,epoch_info=epoch_info,
                    allow_pickle=True)
        else:
            np.savez_compressed(save_cc_name,
                    full_cc_file=full_cc_filename,
                    ccdata=ccdata,epoch_info=epoch_info,
                    core_pos=core_pos,
                    allow_pickle=True)       
        print("Saved: {0}.npz".format(save_cc_name))

    #
    # Plot ccdata before proceeding + do a sanity check...
    #
    #if config['show_results']:
    #   cl.plot_cclist(ccdata,make_square=True, color_epochs=True)
    #   plt.show()
    
    #
    if len(ccdata[ccdata['group'] < 0]) > 0:
        print("ccdata has flagged values:")
        print(ccdata[ccdata['group'] < 0])
        exit(0)

    #-------------------------------------------------------------------------------
    #
    # Setup for clustering
    #
    #--------------------------------------------------------------------------------
    #----------------------------------------------------------------------------
    # Setup a save name for full, merged results
    #
    save_win_results = save_name + ".{0:.2f}-{1:.2f}.merged_win_results".format(min_epoch,max_epoch)

    #----------------------------------------------------------------------------
    # break time range into at set of overlapping 'windows' of epochs to fit
    #
    #. NOTE: If windowing = False, then only a single window covering 
    #        all epochs will be used
    #
    win_info = cl.create_epoch_windows(epoch_info, min_epoch, max_epoch,
                                            windowing = windowing, 
                                            winN=config['window_Nepochs'], 
                                            maxGap=config['maxGap'])
    if config['print_diag']:
        print(pd.DataFrame(win_info))

    #
    # Run cluster fits for a range of possible cluster numbers to *each* window
    #
    #. NOTE: if previous results for a window exist, these will be loaded unless 
    #           ReCalculate_all=True
    #
    #  NOTE: win_info will be updated with best Nclusters estimates for each window
    #          based on bic and bic*.  These are only initial estimates.
    #
    #  --> This is compute intensive and best to use multiple threads, see below
    #
    data_win, epochs_win, cluster_results_win, Ncluster_array_win, results_df_win =\
    cl.run_epoch_window_fits(source, band,
                            win_info, ccdata, epoch_info, min_clusters, max_clusters,
                            Fit_Accel=False,   # Don't fit accelerations in windowed fits????
                            StokesQU_weight=StokesQU_weight,
                            ClusterType=Method, CoreIDMethod=CoreIDMethod,
                            JetDir = config['JetDir'],
                            EGauss=config['EGauss'],
                            complex=config['complex'],
                            overwrite_results = False, SigmaCut=config['SigmaCut'],
                            ReCalculate=ReCalculate_fits,
                            print_diag=config['print_diag'],
                            results_dir=results_dir,
                            input_core_pos=core_pos,
                            save_name=save_name, Threads=config['cpus'], run_history=run_history)
            
    #
    # Load previous Nclusters values and labels if they exist
    #  --> if not, 999 will be used as default labels and Nclusters values
    #
    prev_Nclusters_win, prev_labels_win, prev_cl_df =\
        cl.get_previous_Nclusters_labels(save_win_results+".csv", win_info, max_clusters, 
                                            cluster_results_win, Ncluster_array_win,
                                            ReCalculate_Nwin=ReCalculate_Nwin, 
                                            Recalculate_crossIDs=ReCalculate_crossIDs)
    if config['print_diag']:
        if not(ReCalculate_Nwin):
            print("Previous Nclusters for each window:", prev_Nclusters_win)
        if not(ReCalculate_crossIDs):
            print("Previous labels for each window:", prev_labels_win)


    #
    # Try to find the best Nclusters to use for each window and labels for matching clusters
    #   across neighboring time windows.  This is the key information that links up each
    #   of the otherwise independent window fits to each epoch range.
    #
    # NOTE: windows with previous Nclusters and labels will use those instead
    #
    print("\nFinding best Nclusters for each window and matching cluster labels across windows...")
    N_win, labels_win = cl.cluster_window_matching(ccdata, epoch_info, root_data_dir,
                                                minN=min_clusters, maxN=max_clusters, 
                                                win_info=win_info,       
                                                Ncluster_arrays=Ncluster_array_win, 
                                                cluster_results=cluster_results_win,
                                                prev_Nclusters_win=prev_Nclusters_win,
                                                prev_labels_win=prev_labels_win,
                                                edit_N_win=config['editN'],
                                                flux_log_diff=config['flux_match'], 
                                                size_pos_fact=config['pos_match'],
                                                area_log_diff=config['area_match'],
                                                print_diag=False, run_history=run_history)
    win_info['Nclusters'] = N_win
    if config['print_diag']:
        print("Final Nclusters for each window:", N_win)
        print("Final labels for each window:", labels_win)
    
    print("Total number of cluster epochs:", np.sum(N_win))
    print("Total number of lables assigned:", np.unique(labels_win[labels_win >= 0]).size)
    print("Average number of epochs per label:", np.sum(N_win)/np.unique(labels_win[labels_win >= 0]).size)
    
    #
    # Take the information found above and turn it into a unified cluster property dataframe
    #   calculated directly from the clean components in each epoch *and* the best cluster
    #   model from the corresponding window of epochs around that epoch.
    #
    print("\nConstructing unified cluster/epoch dataframe.")
    cl_df, cc_data, cc_labels = cl.construct_cluster_epoch_df(win_info, 
                                                            N_win=N_win, 
                                                            labels_win=labels_win,
                                                            epoch_info=epoch_info, 
                                                            ccdata_win=data_win,
                                                            prev_cl_df=prev_cl_df,                                                
                                                            Ncluster_arrays=Ncluster_array_win, 
                                                            cluster_results=cluster_results_win,
                                                            relabel_ccdata=False, run_history=run_history)
    
    # NOTE: cc_data and cc_labels are just for convenience here, they are left
    #       with the origID labels from the fitting process to make dynamic labelling
    #       easier later on if the cl_df labels are changed by the user.  See 
    #       overplotting functions in cluster_code.py for how it leverages both OrigID
    #       and the final clusterID from cl_df to accomplish this.

    # default is to save the results if we recalculated anything or any results have otherwise changed
    save_results = 'y'
    if not(ReCalculate_all or ReCalculate_fits or ReCalculate_Nwin or ReCalculate_crossIDs):
        try:
            if pd.testing.assert_frame_equal(cl_df,prev_cl_df) is None:
                print("No changes from saved results.")     
                save_results = 'n'
        except AssertionError:
            print("Changes detected from saved results. Default is to save new results.")

    # ----------------------------------------------------------------------------  
    # Plot results so we can have a look!
    #
    if config['show_results'] or config['editN']:
        #
        sfig,fig = cl.show_clusters(cc_data, epoch_info, root_data_dir, 
                                labels=cc_labels, cluster_epoch_df=cl_df, 
                                flux_threshold=config['flux_threshold'], 
                                show_overlays=True, colorImages=config['colorImages'],
                                run_history=run_history)

        plt.show()
        #
        # Ask if we want to save updated results, if they have changed
        try:
            if pd.testing.assert_frame_equal(cl_df,prev_cl_df) is None:
                print("No changes from saved results.")     
                save_results = 'n'
        except AssertionError:
            print("Changes detected from saved results. ")
            save_results = input("Save updated results? (y/n): ").strip().lower()

    # ----------------------------------------------------------------------------
    # Save the results so we don't have to recalculate them again...
    #
    if save_results in ['y','Y', 'yes', 'Yes']\
        or not(os.path.exists(save_win_results+".csv"))\
        or not(os.path.exists(save_win_results+".plotdata.npz")):
        #
        # Save merged cluster dataframe to CSV, first backup any existing file
        backup_dir = None
        backup_string = None
        if os.path.exists(save_win_results+".csv"):
            backup_dir = results_dir + "backups/"
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
                print(f"Directory '{backup_dir}' created for backup files.")    
            backup_string = "001"
            backup_name = backup_dir + "backup_" + backup_string + "_merged_win_results.csv"    
            while os.path.exists(backup_name):
                backup_string = str(int(backup_string) + 1).zfill(3)
                backup_name = backup_dir + "backup_" + backup_string + "_merged_win_results.csv"    
            os.rename(save_win_results+".csv", backup_name)
            print("Existing results backed up to {0}".format(backup_name.replace("_merged_win_results.csv", "_*.*")))
            shutil.copy(config_file, backup_name.replace("_merged_win_results.csv", "_config.json"))
            shutil.copy(results_dir+"run_string.txt", backup_name.replace("_merged_win_results.csv", "_run_string.txt"))
            run_history.append("# Backed up previous results to {0}\n".format(backup_name.replace("_merged_win_results.csv", "_*.*")))
        #    
        cl_df.to_csv(save_win_results+".csv",index=False)
        print("Saved merged results file to {0}".format(save_win_results+".csv"))
        run_history.append("# Saved current results to {0}\n".format(save_win_results.replace(".merged_win_results", ".*.*")))
        # save supplementary npz file with data needed for plotting
        np.savez_compressed(save_win_results+".plotdata.npz",
                epoch_info=epoch_info,
                cc_data=cc_data,
                cc_labels=cc_labels,
                root_data_dir=root_data_dir,
                allow_pickle=True)
        print("Saved supplementary merged results data to {0}".format(save_win_results+".plotdata.npz"))
        #
        # Save most recent command line
        with open(results_dir + "run_string.txt", 'w') as f:
            f.write(current_run_string)
            print("Saved current run command to {0}".format(results_dir + "last_run_string.txt"))
        #
        # Append run history to a text file in the results directory
        with open(results_dir + "history.txt", 'a') as f:
            f.writelines(run_history)
            print("Appended run history to {0}".format(results_dir + "history.txt"))
        #
        # Save summary plots
        plotfile_prefix = save_name + ".{0:.2f}-{1:.2f}.".format(min_epoch,max_epoch)
        if backup_string is not None:
            shutil.copy(plotfile_prefix+"summary_plots.pdf", backup_dir + "backup_" + backup_string + "_summary_plots.pdf")
            shutil.copy(plotfile_prefix+"epoch_overplots.mp4", backup_dir + "backup_" + backup_string + "_epoch_overplots.mp4")
        save_summary_plots(epoch_info, cc_data, root_data_dir, cl_df, cc_labels, 
                           file_prefix=plotfile_prefix, colorImages=config['colorImages'])
        #
        # Save configuration
        if save_config:
            print("Setting recalulation flags in config file to reflect what was just done...")
            config['recalc_all'] = ReCalculate_all
            config['recalc_fits'] = ReCalculate_fits
            config['recalc_N'] = ReCalculate_Nwin
            config['recalc_IDs'] = ReCalculate_crossIDs
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=4)
            print("Configuration saved to {0}.".format(config_file))

    #----------------------------------------------------------------------------

    #-------------------------------------------------------------------------------
    #
    # Save Results to mySQL database if requested
    #
    #--------------------------------------------------------------------------------
    if config['mySQL_ID'] is not None and band == 'u':
        if not(config['dont_confirm']):
            resp = input("\nLoad results to mySQL under {0}? (y/n): ".format(config['mySQL_ID']))
        else:
            resp = 'y'
        #
        if resp in ['y','Y']:
            cl.update_mySQL(source, config['mySQL_ID'], "components", cl_df, confirm=not(config['dont_confirm']))
        else:
            print("Exiting without saving to mySQL")

