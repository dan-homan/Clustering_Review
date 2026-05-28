#-------------------------------------------------------
#
# cluster_code.py 
#
# Set of functions and definitions for clustering
#  algorithms for clean components, both in individual
#  epochs and over time
#
# D.C. Homan,  homand@denison.edu
#
CCVERS = "2026-05-07"

def setup_cluster_code():
    global CCVERS
    print("cluster_code.py file version: {0}".format(CCVERS))

    return CCVERS
          
#
# Change Log
# -----------
# See cluster_code_changelog.txt for updates
#
# IMPORTANT NOTE:  Find_Static_Clusters  needs updating
#                  for output format and to use fluxes
#                  of the clean components
#
# 

#-------------------------------------------------------
#  import libraries needed by functions below
#-------------------------------------------------------

import numpy as np
import scipy
import matplotlib.pyplot as plt

plt.rcParams['figure.figsize'] = [9, 6]
plt.rcParams['figure.dpi'] = 150

import matplotlib.patches as patches
from matplotlib.widgets  import Slider, LassoSelector
from matplotlib.path import Path
from matplotlib.colors import SymLogNorm

import colormaps as cmaps
from matplotlib import cm 

import sklearn.cluster as cluster
import sklearn.mixture as mixture
#import hdbscan
from astropy import units as u
from astropy.wcs import WCS
from astropy.io import fits
from astropy.time import Time
from matplotlib.transforms import Affine2D
from astropy.visualization.wcsaxes import WCSAxes
from scipy import ndimage
from scipy.ndimage import gaussian_filter
from scipy import stats as scipy_stats
import pandas as pd
import glob
import os

from joblib import Parallel, delayed
from sqlalchemy import create_engine
from sqlalchemy import text
import getpass

#------------------------------------------------------
# Define datatypes used in many functions
#------------------------------------------------------
#
# Datatype for clusters
#
cluster_datatype = np.dtype(dtype = {'names':('epoch','centX','centY','dcentX','dcentY',
                                              'slopeX','slopeY','dslopeX','dslopeY',
                                              'accelX','accelY','daccelX','daccelY','medianFlux',
                                              'sizeMaj','sizeMin','sizePA','label'),
                            'formats':('f8','f8','f8','f8','f8','f8','f8','f8',
                                       'f8','f8','f8','f8','f8','f8','f8','f8',
                                       'f8','i') })
#
# Datatype for clean components
#
cc_datatype = np.dtype(dtype = {'names':('epoch','x','y','stokes','flux','sizex','sizey','group','clusterID'),
                                 'formats':('f8','f8','f8','U1','f8','f8','f8','f8','i','i') })

#
# Datatype for epoch information
#
epoch_datatype = np.dtype(dtype = {'names':('epoch_name','epoch_val','band','cc_file','fits_file',
                                         'inoise','pnoise', 'sigma_cut','sigma_cut_area',
                                         'bmaj','bmin','bpa','pix_to_mas'),
                                 'formats':('U10','f8','U1','U200','U200','f8','f8','f8','f8','f8','f8','f8','f8') })

#
# Datatype for epoch window information
#
window_datatype = np.dtype(dtype = {'names':('winID','first_epoch','median_epoch','last_epoch',
                                             'num_epochs','Nclusters'),
                                   'formats':('i','f8','f8','f8','i','i') })

# Datatype for core positions
#
corepos_datatype = np.dtype(dtype=[('x', 'f8'), ('y', 'f8')])

#
# Define colors to use when plotting clusters
#. --> save cyan 'c' for non-robust clusters
#
cl_colors = ['b', 'g', 'r', 'm', 'y', 'gray']
cl_markers = ['x','o','s','o','s','p','*','^','v',
              '*','^','v','X', 'D', 'P', 'D', '1']
cl_fill = ['none','full','none','none','full','none','full','none','full',
           'none','full','none','full', 'none','full','full','none']

#------------------------------------------------------ 
#  epoch_convert
#------------------------------------------------------ 
# Function for converting epoch strings to floats.
#
# Use julian year to avoid small offsets due to previous strategy
#
def epoch_convert(epoch_string):

    decyear = Time(epoch_string.replace('_','-')).jyear
    
    return np.round(decyear,4)

#------------------------------------------------------
# get_image_data
#------------------------------------------------------
#
# Function to get image data from fits file
def get_image_data(fits_file):
    if fits_file.header['NAXIS'] == 4:
        fits_data = fits_file.data[0][0]
    elif fits_file.header['NAXIS'] == 2:
        fits_data = fits_file.data
    else:
        print("Image NAXIS = {0} not recognized".format(fits_file.header['NAXIS']))
        return None

    return fits_data

#------------------------------------------------------
# set_image_data
#------------------------------------------------------
#
# Function to set image data in a fits file
def set_image_data(fits_file, image_data):
    if fits_file.header['NAXIS'] == 4:
        fits_file.data[0][0] = image_data
    elif fits_file.header['NAXIS'] == 2:
        fits_file.data = image_data
    else:
        print("Image NAXIS = {0} not recognized".format(fits_file.header['NAXIS']))
        return None

    return fits_file
#------------------------------------------------------ 
#  Noise estimates from images
#------------------------------------------------------ 
#
#
# Function to get a noise estimate from the 1st through Nth highest peaks in an image
#    
#   --> returns noise estimate based on first Nth highest peaks and the
#       the number standard deviations that each peak represents
#
#.  --> Assumes the entire array sent here is >= 0, with exactly 0
#       representing masked pixels, so we assume the tail represents
#       the cummulative point in a half-normal distribution
# 
#   --> Only the central square of the image, near the phase center is considered
#       I have currently choosen a square +/- 15 beam_width on either side of
#       the phase center = 900 beam areas.  The logic here is that 
#       errors near the center of the map are likely to be larger.
#
#   --> True source structure is assumed to be masked out before sending
#       image to this function
#
#.  --> Additional masking happens of max pixel + beam area around it
#       for each of the 1, 2, ... N-1 peaks
#
def get_Nth_highest_noise_est(fits_file, ifits_file):
    
    # Load data + I-data for masking
    
    fits_data = get_image_data(fits_file)   
    ifits_data = get_image_data(ifits_file)


    # mask pixel array by setting locations with I >= 0 to 0, and make array all positive
    pixel_array = fits_data.copy()
    pixel_array[(ifits_data >= 0)] = 0
    pixel_array = np.abs(pixel_array)
    
    if np.sum(pixel_array > 0) == 0:
        print("No negative pixels found in I image for noise estimate, no noise tail available")
        return 0.0  

    # find the beam size for masking out N-1 peaks
    if not('BMAJ' in fits_file.header and 'BMIN' in fits_file.header and 'CDELT2' in fits_file.header): 
        print("No beam information in header, estimating beam width as 5 pixels")
        beam_width = 5.0
    else:
        bmaj_fwhm_pix = fits_file.header['BMAJ']/fits_file.header['CDELT2']
        bmin_fwhm_pix = fits_file.header['BMIN']/fits_file.header['CDELT2']
        beam_width = np.sqrt(bmaj_fwhm_pix*bmin_fwhm_pix) 
    #
    buffer = int(beam_width/2 + 0.5)
    beam_area = beam_width*beam_width

    # Define a square box near the phase center where we will look for these peaks
    #  --> the logic here is that we are studying phenomena which tend to be stronger
    #      near the phase center of the image, creating the noise tail.  
    #  --> default to +/- 15 * beam_width = 30 beam_widths on a side = 900 beam area.
    peak_search_distance = 15.0*beam_width
    # don't exceed image limits
    if peak_search_distance < fits_file.header['CRPIX2'] and \
       peak_search_distance < fits_file.header['NAXIS2']-fits_file.header['CRPIX2'] and \
       peak_search_distance < fits_file.header['NAXIS1']-fits_file.header['CRPIX1'] and \
       peak_search_distance < fits_file.header['CRPIX2']:
        # only consider pixels in that box area = (2*peak_search_distance)**2 when computing
        #   the statistics of how likely these peaks are...
        pixel_array[0:int(fits_file.header['CRPIX2']-peak_search_distance),:] = 0
        pixel_array[int(fits_file.header['CRPIX2']+peak_search_distance):-1,:] = 0
        pixel_array[:,0:int(fits_file.header['CRPIX1']-peak_search_distance)] = 0
        pixel_array[:,int(fits_file.header['CRPIX1']+peak_search_distance):-1] = 0

    # Verify that masking is working the way is should...
    #plt.imshow(pixel_array)
    #fig.show()
    
    # Compute the number of beams searched for these peaks
    num_of_beams_searched = np.sum(pixel_array > 0)/beam_area

    # arrays for N peaks we will test
    N = 5                     # number of peaks to consider
    max_val = np.zeros(N)     # value of peak
    noise_est = np.zeros(N)

    # Loop over 1st through Nth peaks to find them 
    #  and compute the statistics they imply
    copy_of_pixels = pixel_array.copy()
    for i in range(N-1):
        # first find the peak 
        max_val[i] = np.max(copy_of_pixels)
        max_loc = np.unravel_index(np.argmax(np.abs(copy_of_pixels), axis=None), copy_of_pixels.shape)

        # compute the noise estimate for finding a ith peak of this value
        fraction = 1.0 - i/num_of_beams_searched
        num_std_dev = scipy_stats.halfnorm.ppf(fraction)
        noise_est[i] = max_val[i]/num_std_dev

        # mask 1 square ~ beam area around max to be 0 so we can find the next peak
        ymin = np.max([0,max_loc[0]-buffer])
        ymax = np.min([fits_file.header['NAXIS2']-1,max_loc[0]+buffer])
        xmin = np.max([0,max_loc[1]-buffer])
        xmax = np.min([fits_file.header['NAXIS1']-1,max_loc[1]+buffer])
        copy_of_pixels[ymin:ymax+1,xmin:xmax+1] = 0
    
    # return median noise estimate of the N peaks we tested
    return np.median(noise_est)

#
# Function to estimate noise values in i, q, and u maps and
#   to apply some sanity checks for excessive noise in the 
#.  tails of the distribution due to deconvolution or dterm
#.  type errors that may not be uniform across the map.
#
def get_noise_estimates(fits_file, ifits_file):
    #
    # Estimate a good number for noise expected in images
    #
    
    # Start a with noise estimate from the whole map using MAD
    def return_zero(x,axis):
        return 0
    noise=scipy_stats.median_abs_deviation(get_image_data(fits_file), scale='normal', axis=None, center=return_zero)
           
    # Next look at the tail of the noise distribution for another estimate of sigma
    tail_noise = get_Nth_highest_noise_est(fits_file, ifits_file) 
      
    print("MAD map noise = {0:.3f} mJy/beam, Noise tail sigma = {1:.3f} mJy/beam".format(1000*noise, 1000*tail_noise))    

    return np.max([noise, tail_noise]), noise, tail_noise



#------------------------------------------------------
#  Beam estimate from MOJAVE XX paper 
#   --> declination should be in J2000 coordinates
#
def est_beam_size(dec, band):
    
    bmaj = 1.283 - 8.950e-3*dec - 7.914e-5*(dec**2) + 1.245e-6*(dec**3)
    bmin = 0.522 + 1.007e-3*dec + 8.884e-6*(dec**2) - 5.571e-8*(dec**3)

    if band=='k':
        factor = (15.4/23.8)
    elif band=='q':
        factor = (15.4/43.2)
    elif band=='u':
        factor = 1
    else:
        print("Band not recognized")
        factor = None
    
    return factor*bmaj, factor*bmin

#------------------------------------------------------ 
#  get_cc_list
#------------------------------------------------------
# This function reads a clean component file from Difmap OR
#   if "use_pixels" = True, generates a pixel-based list of components
#
#  Records the (x,y) position and flux of every component stronger 
#  than "cutoff".  Component sizes are assumed to be zero.
#
#  A structured numpy array, with the fields described below, is returned 
#  use in other functions.
#
#    dtype = {'names':('epoch','x','y','flux','sizex','sizey','group'),
#             'formats':('f8','f8','f8','f8','f8','f8','i') })
#
#  group = 0   is assigned to data that is not flagged
#  group = -1  are data that are flagged relative to the map noise in that epoch
#                based on flag_sigma OR due to the overall "cutoff" value
#  group = -2  are data flagged in the 'flag_outliers' procedure that includes
#                information from other epochs -- NOTE: this only works if there
#                is not much shift in position between epoch maps
#
def get_cc_list(source, epoch_list, band, 
                suffix=".icn.mod", image_suffix=".icn.fits.gz",
                flag_cutoff=0.0, core_correct=False, flag_sigma=5.0,
                print_result=False, show_plot=False, include_QU=False,
                use_pixels=False, pixel_std_image_suffix=None, logfile=None):
    #
    # define a structured array to store the data
    # 
    ccdata = np.array([], dtype = cc_datatype)
    epoch_info = np.array([], dtype = epoch_datatype)
    inoise = np.full(len(epoch_list), np.nan)
    pnoise = np.full(len(epoch_list), np.nan)

    # Loop over each epoch and get the clean component data..
    for i in range(len(epoch_list)):
        epoch_name = epoch_list[i]
        epoch_val = epoch_convert(epoch_name)
        print("Working on {0}, {1}".format(epoch_name,epoch_val))
        if logfile is not None:
            logfile.write("# Working on {0}, {1}\n".format(epoch_name,epoch_val))
        xcore = 0
        ycore = 0
        if core_correct:
            # find epoch position offset from the core positions file...
            core_posfile = open(corefile,"r")
            no_corepos = True
            for line in core_posfile:
                vals = line.split()
                if source in vals[0]:
                    if epoch in vals[1]:
                        xcore = np.float64(vals[7])
                        ycore = np.float64(vals[8])
                        no_corepos = False
                        break
            if no_corepos:
                print("No core position found for {0} in {1}".format(source,epoch))
                if logfile is not None:
                    logfile.write("# No core position found for {0} in {1}\n".format(source,epoch))

            else:
                print("Found core position ({0},{1})".format(xcore,ycore))
                if logfile is not None:
                    logfile.write("# Found core position ({0},{1})\n".format(xcore,ycore))  
        else:
            print("No correction for core position applied")
            if logfile is not None:
                logfile.write("# No correction for core position applied\n")

        # put together the appropriate clean component filename and corresponding image
        imod_file = epoch_name + '/' + source + '.' + band + '.' + epoch_name + suffix        
        iimage_file = epoch_name + '/' + source + '.' + band + '.' + epoch_name + image_suffix
        if use_pixels and pixel_std_image_suffix is not None:
            iimage_std_file = epoch_name + '/' + source + '.' + band + '.' + epoch_name + pixel_std_image_suffix

        # Open the image and model files on disk 
        if not(os.path.exists(iimage_file)):
            print("   No image file found {0}, skipping epoch...".format(iimage_file))
            if logfile is not None:
                logfile.write("# No image file found {0}, skipping epoch...\n".format(iimage_file)) 
            continue
        if not(use_pixels) and not(os.path.exists(imod_file)):
            print("   No clean component file found {0}, skipping epoch...".format(imod_file))
            if logfile is not None:
                logfile.write("# No clean component file found {0}, skipping epoch...\n".format(imod_file)) 
            continue
        if use_pixels and pixel_std_image_suffix is not None and not(os.path.exists(iimage_std_file)):
            print("   No pixel std. image file found {0}, skipping epoch...".format(iimage_std_file))
            if logfile is not None:
                logfile.write("# No pixel std. image file found {0}, skipping epoch...\n".format(iimage_std_file))
            continue

        ifits = fits.open(iimage_file)[0]
        
        # Get pixel to mas conversion factors
        pix_to_mas_x = ifits.header['CDELT1']*(60*60*1000)
        pix_to_mas_y = ifits.header['CDELT2']*(60*60*1000)
        #
        # if no beam size in image header, use est_beam_size function as an estimate
        #
        if 'BMAJ' in ifits.header and 'BMIN' in ifits.header and 'BPA' in ifits.header:
            bmaj = ifits.header['BMAJ']*(60*60*1000)
            bmin = ifits.header['BMIN']*(60*60*1000)
            bpa = ifits.header['BPA']
            print("   Beam size from header: {0:.2f} x {1:.2f} mas at {2:.2f} deg".format(bmaj,bmin,bpa))
            if logfile is not None:
                logfile.write("# Beam size from header: {0:.2f} x {1:.2f} mas at {2:.2f} deg\n".format(bmaj,bmin,bpa))
            if use_pixels: 
                print("   Image units converted to Jy/beam from Jy/pixel for pixel-based components")
                if logfile is not None:
                    logfile.write("# Image units converted to Jy/beam from Jy/pixel for pixel-based components\n")  
            convert_pixel_flux = np.abs(pix_to_mas_y*pix_to_mas_x)/(np.pi*bmaj*bmin/(4.0*np.log(2)))
        else:
            bmaj,bmin = est_beam_size(ifits.header['OBSDEC'], band)
            bpa = 0.0
            print("   No beam size in header, using  {0:.2f} x {1:.2f} mas at {2:.2f} deg as an estimate".format(bmaj,bmin,bpa))
            if logfile is not None:
                logfile.write("# No beam size in header, using  {0:.2f} x {1:.2f} mas at {2:.2f} deg as an estimate\n".format(bmaj,bmin,bpa))
            convert_pixel_flux = 1.0 

        # Estimate the noise in the image
        if use_pixels and pixel_std_image_suffix is not None:
            ifits_std = fits.open(iimage_std_file)[0]
            # image pixels and stdev pixels
            img_pixels = get_image_data(ifits) 
            std_pixels = get_image_data(ifits_std)
            inoise[i] = np.mean(std_pixels)
            # create convolved versions...
            #conv_img_pixels = gaussian_filter(img_pixels, sigma=[bmaj/np.abs(pix_to_mas_y*2.355),bmin/np.abs(pix_to_mas_x*2.355)])
            #conv_std_pixels = gaussian_filter(std_pixels, sigma=[bmaj/np.abs(pix_to_mas_y*2.355),bmin/np.abs(pix_to_mas_x*2.355)])
            # valid locations in the map must be above flag_sigma * noise estimate in both convolved and original images
            #conv_inoise = np.mean(conv_std_pixels) 
            # conv_inoise is already in units of Jy/beam based on how scipy gaussian_filter works
            #inoise[i] = np.mean(std_pixels)
            #
            # valid pixels must be above flag_sigma * inoise in original image
            valid_pix = (img_pixels >= flag_sigma*inoise[i]) 
            # scale inoise[i] to Jy/beam units. 
            #  assume it scales like sqrt(beam area / pixel area)
            #  for use in bic* Ndata calculations which are given by
            #     Ndata = total_flux / (flag_sigma*noise_per_beam)
            inoise[i] *= np.sqrt((np.pi*bmaj*bmin/(4.0*np.log(2)))/(np.abs(pix_to_mas_y*pix_to_mas_x)))
            #
            print("   Using pixel std. image for noise estimate: {0:.3f} mJy/beam".format(1000*inoise[i]))
            if logfile is not None:
                logfile.write("# Using pixel std. image for noise estimate: {0:.3f} mJy/beam\n".format(1000*inoise[i]))
        else:
            inoise[i],irms,inoise_tail = get_noise_estimates(ifits, ifits)
            # Document noise estimate in log file
            if logfile is not None:
                logfile.write("# I noise estimate: {0:.3f} mJy/beam\n".format(1000*inoise[i]))

        #    
        # Find area in mas**2 that is above the sigma_cut threshold
        sigma_cut_area = np.sum(get_image_data(ifits) > (flag_sigma*inoise[i]))*(pix_to_mas_y**2)

        # define stokes list to process
        stokes_list = [ "i" ]
        
        # are there polarization images?
        if include_QU and "icn" in imod_file and "icn" in iimage_file:
            qmod_file = imod_file.replace("icn","qcn")
            qimage_file = iimage_file.replace("icn","qcn")
            umod_file = imod_file.replace("icn","ucn")
            uimage_file = iimage_file.replace("icn","ucn")
    
            if (os.path.exists(qmod_file) or use_pixels) and os.path.exists(qimage_file) and\
               (os.path.exists(umod_file) or use_pixels) and os.path.exists(uimage_file):
                stokes_list = [ "i", "q", "u" ]
                qfits = fits.open(qimage_file)[0]
                ufits = fits.open(uimage_file)[0]
                print(qmod_file,"\n",qimage_file)
                qnoise,qrms,qnoise_tail = get_noise_estimates(qfits, ifits)
                print(umod_file,"\n",uimage_file)
                unoise,urms,unoise_tail = get_noise_estimates(ufits, ifits)
                if logfile is not None:
                    logfile.write("# Q noise estimate: {0:.3f} mJy/beam\n".format(1000*qnoise))
                    logfile.write("# U noise estimate: {0:.3f} mJy/beam\n".format(1000*unoise))
                #
                # Calc. P and correct Rician Bias
                #
                Pdata = np.sqrt((get_image_data(qfits))**2 + (get_image_data(ufits))**2)
                #pnoise = (qnoise + unoise)/2
                pnoise[i] = np.sqrt((qnoise**4+unoise**4)/(qnoise**2+unoise**2))
                if logfile is not None:
                    logfile.write("# P noise estimate: {0:.3f} mJy/beam\n".format(1000*pnoise[i]))
                #
                Pdata_sqr = (Pdata**2 - pnoise[i]**2)
                Pdata_sqr[Pdata_sqr < 0.0] = 0.0
                Pdata = np.sqrt(Pdata_sqr)
                print("Correcting Rician Bias in Calculated P")
                if logfile is not None:
                    logfile.write("# Correcting Rician Bias in Calculated P\n")
        
        # append values to epoch_info structure
        cdir = 'data/'+source+'/'
        if band in ['q','k']:
            cdir = band + cdir
        epoch_info = np.append(epoch_info, np.array([(epoch_name, epoch_val, band,
                                                     cdir+imod_file,cdir+iimage_file,
                                                     inoise[i], pnoise[i],
                                                     flag_sigma,sigma_cut_area,
                                                     bmaj,bmin,bpa,
                                                     pix_to_mas_y)], dtype=epoch_datatype))
        #
        # loop over stokes to find component information in this epoch
        #
        for stokes in stokes_list:
            if stokes == "q":
                if not(use_pixels):
                    cc_file = open(qmod_file,"r")
                test_image = Pdata
                image = get_image_data(qfits)
                noise = pnoise[i]
            elif stokes == "u":
                if not(use_pixels):
                    cc_file = open(umod_file,"r")
                test_image = Pdata
                image = get_image_data(ufits)
                noise = pnoise[i]
            else:
                if not(use_pixels):
                    cc_file = open(imod_file,"r")
                test_image = get_image_data(ifits)
                image = get_image_data(ifits)   
                noise = inoise[i]
      
            #
            # if we are using pixels, record those above noise cuttoffs
            #
            if use_pixels:
                # find pixel locations above cutoff in test_image 
                good_pixels = np.argwhere(valid_pix)
                #
                # Review image and test_image to verify that cutoffs are working as expected
                if False:
                    norm = SymLogNorm(linthresh=1e-3, vmin=np.min(test_image), vmax=np.max(test_image), base=10)
                    im = plt.imshow(test_image, origin='lower', cmap='viridis', norm=norm)
                    plt.colorbar(im, label='Flux (Jy/beam)')
                    plt.show()
                # loop over good_pixels and record values
                for pix in good_pixels:
                    #
                    # assign values for this component
                    #  --> note that pixel locations start at zero in python
                    #
                    group = 0
                    xpix = pix[1]
                    ypix = pix[0] 
                    flux = image[ypix,xpix]*convert_pixel_flux 
                    xcoord = (xpix + 1 - ifits.header['CRPIX1'])*pix_to_mas_x
                    ycoord = (ypix + 1 - ifits.header['CRPIX2'])*pix_to_mas_y
                    #
                    # Append this component to the list
                    #. --> correct for core position if needed!!
                    #
                    ccdata = np.append(ccdata, np.array([(epoch_val,
                                                        xcoord-xcore,ycoord-ycore,
                                                        stokes,
                                                        flux,
                                                        0.0,0.0,group,-1)], 
                                                        dtype=ccdata.dtype))
            # 
            # If we are using clean components from file, read them in and test for significance
            #
            else:
                for line in cc_file:
                    vals = line.split()
                    if "!" in vals[0]:
                        continue
                    #
                    # assign values for this feature
                    #
                    group = 0
                    flux = np.float64(vals[0])
                    xcoord = np.float64(vals[1])*np.sin(np.float64(vals[2])*np.pi/180)
                    ycoord = np.float64(vals[1])*np.cos(np.float64(vals[2])*np.pi/180)
                    #
                    # Check flux of pixel vs. noise level at that location
                    #
                    xpix = np.int32(ifits.header['CRPIX1'] - 1 + xcoord/pix_to_mas_x)
                    ypix = np.int32(ifits.header['CRPIX2'] - 1 + ycoord/pix_to_mas_y)
                    #
                    # indicate cc is flagged if it falls below cutoffs
                    #  --> IMPORTANT: require polarized cc to fall in acceptable
                    #                 areas of the I image as well.
                    #
                    if (flux < flag_cutoff and stokes == "i") or test_image[ypix,xpix] < flag_sigma*noise\
                        or get_image_data(ifits)[ypix,xpix] < flag_sigma*inoise[i]:
                        group = -1 
                        continue  # skip flagged components and do not record them      
                    
                    #
                    # Append this component to the list
                    #. --> correct for core position if needed!!
                    #
                    ccdata = np.append(ccdata, np.array([(epoch_val,
                                                        xcoord-xcore,ycoord-ycore,
                                                        stokes,
                                                        flux,
                                                        0.0,0.0,group,-1)], 
                                                        dtype=ccdata.dtype))

                            
    #
    # Report summary information 
    #     
    print("Found {0} comps in total.".format(len(ccdata)))
    print("      {0} with > {1} Jy at image locations > {2} sigma!".format(len(ccdata[ccdata['group']==0]),
                                                                                 flag_cutoff,flag_sigma))
    if logfile is not None:
        logfile.write("# Found {0} comps in total.\n".format(len(ccdata)))
        logfile.write("#       {0} with > {1} Jy at image locations > {2} sigma!\n".format(len(ccdata[ccdata['group']==0]),
                                                                                             flag_cutoff,flag_sigma))
                
    if print_result:
        print("\nCC Values: (epoch, x, y, stokes, flux, size_x, size_y, group, label)\n\n",
              ccdata[ccdata['group']==0],"\n")
        if logfile is not None:
            logfile.write("\nCC Values: (epoch, x, y, stokes, flux, size_x, size_y, group, label)\n\n{0}\n".format(
                ccdata[ccdata['group']==0]))

    if show_plot:
        plot_cclist(ccdata,make_square=True)
        
    return ccdata, epoch_info

#------------------------------------------------------ 
# get_source
#------------------------------------------------------ 
# This function sets up and runs get_cc_list for
# a given source between a given minimum and maximum epoch.
# It will also attempt to identify and flag outlier
# components based on the total flux within a certain
# radius of the component in question
#
# It returns a list of epochs and the clean component
# data in a structured array format as follows:
#
#    dtype = {'names':('epoch','x','y','flux','sizex','sizey','group'),
#             'formats':('f8','f8','f8','f8','f8','f8','i') })
#
def get_source(sourcename, band = 'u',
               directory = None,
               cc_suffix = ".icn.mod",
               image_suffix = ".icn.fits.gz",
               flag_cutoff = 0.0,
               flag_sigma = 5.0,
               core_correct=False,
               show_plot=False,
               sfile = None,
               include_QU=False,
               use_pixels=False,
               pixel_std_image_suffix=None,
               min_Nepoch=5,
               epochs_to_hide=[],
               logfile=None,
               min_epoch=0,
               max_epoch=3000):
    #
    curr_dir = os.getcwd()
    #
    # Change to data directory + sourcename
    #
    if directory:
        #
        # setup directories and files
        #
        if band == 'k':
            data_dir = "Kdata/"
        elif band == 'q':
            data_dir = "Qdata/"
        else:
            data_dir = "data/"
        #    
        if directory[-1] == '/':
            os.chdir(directory+data_dir+sourcename)
        else:
            os.chdir(directory+'/'+data_dir+sourcename)
    #
    # Get a list of epoch directories 
    #
    epoch_dirs = glob.glob("????_??_??")
    epoch_dirs.sort()
    #print(epoch_dirs)
    #
    # convert into a numerical list of epochs, compare to epoch range
    #
    epoch_list = np.array([])
    for epoch_name in epoch_dirs:
        if epoch_name in epochs_to_hide:
            print("Hiding epoch {0} based on user input".format(epoch_name))
            if logfile is not None:
                logfile.write("# Hiding epoch {0} based on user input\n".format(epoch_name))
            continue
        epoch_val = epoch_convert(epoch_name)
        if epoch_val < max_epoch and epoch_val > min_epoch:
            epoch_list = np.append(epoch_list,epoch_name)
            
    epoch_list.sort()
    print(epoch_list)
    #
    # check that we have enough epochs to process
    if len(epoch_list) < min_Nepoch:
        print("Only {0} epochs found for {1} in range {2} to {3}, skipping source...".format(len(epoch_list),
                                                                                             sourcename,
                                                                                             min_epoch,
                                                                                             max_epoch))
        if logfile is not None:
            logfile.write("# Only {0} epochs found for {1} in range {2} to {3}, skipping source...\n".format(len(epoch_list),
                                                                                                         sourcename,
                                                                                                         min_epoch,
                                                                                                         max_epoch))
        return None, None   
    #
    # Read in the data from the clean component files
    #
    ccdata, epoch_info = get_cc_list(sourcename,epoch_list,band,cc_suffix,
                         image_suffix,flag_cutoff,core_correct,flag_sigma,
                         True, show_plot, include_QU, use_pixels, pixel_std_image_suffix,logfile)
           
    if directory:
        os.chdir(curr_dir)

   
    if sfile is not None:
        np.savez_compressed(sfile,vers=CCVERS,flag_cutoff=flag_cutoff,
                       flag_sigma=flag_sigma,flag_outliers=False,
                       outlier_radius_beams = 0.0,
                       full_data=ccdata,full_ep_info=epoch_info,
                       allow_pickle=True)
        print("Saved: {0}.npz".format(sfile))
        if logfile is not None:
            logfile.write("# Saved: {0}.npz\n".format(sfile))
        
    return epoch_info, ccdata

#------------------------------------------------------ 
# select_epoch_range
#------------------------------------------------------ 
# The goal of this function is just to slice a large
# clean component dataset for a particular range of epochs.
#
# This allows one to load an entire source at once but
# later select a group of epochs for processing
#
# It returns the sliced data and a suggested reference epoch
#
def select_epoch_range(ccdata, ep_info, start, stop, groups=None, input_core_pos=None, show_info=True, run_history=None):
    # set default groups if none provided
    if groups is None:
        groups = [0]  # default to only unflagged data

    # Select the data included in the start, stop range, endpoints included
    data_range = ccdata[(ccdata['epoch']>=start)&(ccdata['epoch']<=stop)].copy()
    epoch_range = ep_info[(ep_info['epoch_val']>=start)&(ep_info['epoch_val']<=stop)].copy()
    if input_core_pos is not None:
        core_pos = input_core_pos[(ep_info['epoch_val']>=start)&(ep_info['epoch_val']<=stop)].copy()
    else:
        core_pos = None
    #
    # filter based on component labels
    #   --> note that component labels < 0 indicate flagged data
    #
    group_mask = (data_range['group'] == -1234)  # should be a binary "False" mask
    # add in "True" for data that match a group in groups
    for group in groups:
        group_mask |= (data_range['group'] == group)
    data_range = data_range[group_mask].copy()
    #
    if show_info:
        print("Filtered to only include components in groups = {0}".format(groups))
        print("  Note: components with group < 0 indicated flagged data")  
        # print epochs found    
        print("Including data from epochs:\n{0}".format(epoch_range['epoch_val']))
        if run_history is not None:
            run_history.append("# Including data from epochs:")
            for i in range(len(epoch_range)):
                if (i%7 == 0):
                    run_history.append("\n# ")
                if i == len(epoch_range)-1:
                    run_history.append(" {0:.4f}\n".format(epoch_range[i]['epoch_val']))
                else:
                    run_history.append(" {0:.4f},".format(epoch_range[i]['epoch_val']))


    return data_range, epoch_range, core_pos

# 
# Define a function for setting axis plotting limits
# 
def set_axis_lim(ax, data, make_square=True, equal_axis=True):
    #
    # Find the range of the data and set limits 
    #
    minx = np.min(data['x'])
    miny = np.min(data['y'])
    maxx = np.max(data['x'])
    maxy = np.max(data['y'])
 
    if make_square:
        spanx = maxx-minx
        spany = maxy-miny
        if spanx < spany:
            maxx += (spany-spanx)/2
            minx -= (spany-spanx)/2
        else:
            maxy += (spanx-spany)/2
            miny -= (spanx-spany)/2
    ax.set_xlim(minx-(maxx-minx)*0.1, maxx+(maxx-minx)*0.1)
    ax.set_ylim(miny-(maxy-miny)*0.1, maxy+(maxy-miny)*0.1)
    ax.invert_xaxis()
    if equal_axis:
        ax.set_aspect("equal")


#
# Define a quick plotting function for components
#
def plot_cclist(data, make_square=True, fig=None, ax=None, color_epochs=False, 
                core_pos=None, PlotFlagged=True, show_plot=True):

    if fig is None or ax is None:
        fig, ax = plt.subplots()

    # copy data
    ccdata = data.copy()
    # adjust if core_pos are provided
    if core_pos is not None:
        unique_epochs = np.sort(np.unique(ccdata['epoch']))
        for j in range(len(unique_epochs)):
            epoch_mask = (ccdata['epoch'] == unique_epochs[j]) 
            #
            # MUST access arrays with masks in this order to
            #   *change* values...
            #
            ccdata['x'][epoch_mask] -= core_pos['x'][j]
            ccdata['y'][epoch_mask] -= core_pos['y'][j]
            #print(core_pos[j])

    # Include flagged components in plot if requested
    if PlotFlagged:    
        # Plot positive and negative of initial flagged group separately
        ccmask = (ccdata['group']==-1)&(ccdata['flux']>0)
        ax.plot(ccdata[ccmask]['x'],ccdata[ccmask]['y'], 
                    color='grey',marker='o',ls='none',fillstyle="none",alpha=0.3,zorder=0)
        ccmask = (ccdata['group']==-1)&(ccdata['flux']<0)
        ax.plot(ccdata[ccmask]['x'],ccdata[ccmask]['y'], 
                    color='red',marker='o',ls='none',fillstyle="none",alpha=0.3,zorder=1)
        # Plot second flagged group
        ccmask = (ccdata['group']==-2)
        ax.scatter(ccdata[ccmask]['x'],ccdata[ccmask]['y'], 
                    color='k',marker='x',alpha=0.8,zorder = 5)
        
    # Plot acceptable components last
    ccmask = (ccdata['group']>=0)
    if color_epochs:
        scat = ax.scatter(ccdata[ccmask]['x'],ccdata[ccmask]['y'],
                   c=ccdata[ccmask]['epoch'], marker="+",alpha=1)
        fig.subplots_adjust(right=0.88)
        cbar_ax = fig.add_axes([0.9, 0.15, 0.02, 0.7])
        cb=fig.colorbar(scat, cax=cbar_ax)
        cb.ax.tick_params(labelsize='small')
    else:
        scat = ax.scatter(ccdata[ccmask]['x'],ccdata[ccmask]['y'], 
                c=ccdata[ccmask]['group'],marker='+', alpha=1,zorder = 10)

    #
    # Define rest of plot
    #
    set_axis_lim(ax, ccdata[ccmask])

    ax.set_xlabel("Relative RA [mas]")
    ax.set_ylabel("Relative Dec [mas]")
    
    if show_plot:
        fig.show()
    
    return fig, ax, scat 

#------------------------------------------------------
#
# Two function to allow selection and editing of cc points
#
#-------------------------------------------------------

#
# Modified rom example: https://matplotlib.org/stable/gallery/widgets/lasso_selector_demo_sgskip.html

class SelectFromCollection:
    """
    Select indices from a matplotlib collection using `LassoSelector`.

    Selected indices are saved in the `ind` attribute. 

    Note that this tool selects collection objects based on their *origins*
    (i.e., `offsets`).

    Parameters
    ----------
    ax : `~matplotlib.axes.Axes`
        Axes to interact with.
    collection : `matplotlib.collections.Collection` subclass
        Collection you want to select from.
    data : `~numpy.ndarray`
        Data to be plotted. This is used to update the collection after
        selection.
    """

    def __init__(self, ax, collection, data):
        self.canvas = ax.figure.canvas
        self.collection = collection
        self.ax = ax
        self.data = data
        self.selpts = ax.scatter([], [], color='black', marker='x')

        self.xys = collection.get_offsets()
        self.Npts = len(self.xys)

        # Ensure that we have separate colors for each object
        self.fc = collection.get_facecolors()
        if len(self.fc) == 0:
            raise ValueError('Collection must have a facecolor')
        elif len(self.fc) == 1:
            self.fc = np.tile(self.fc, (self.Npts, 1))

        self.lasso = LassoSelector(ax, onselect=self.onselect)
        self.ind = np.array([], dtype=int)

    def onselect(self, verts):
        # 
        if self.selpts is not None:
            self.selpts.remove()
        # get the indices of the selected points
        path = Path(verts)
        self.ind = np.append(self.ind, np.nonzero(path.contains_points(self.xys))[0])
         # put x symbols over selected points
        self.selpts = self.ax.scatter(self.xys[self.ind, 0], self.xys[self.ind, 1],
                    color='black', marker='x', zorder=100)
        self.canvas.draw_idle()

    def disconnect(self):
        self.lasso.disconnect_events()
        self.canvas.draw_idle()

    def flag_selected(self):
        # delect points from the data in the selector
        self.data = np.delete(self.data, self.ind)
        # remove the flagged points from the plot itself
        mask = np.ones(self.Npts, dtype=bool)
        mask[self.ind] = False
        self.collection.set_offsets(self.xys[mask])
        self.xys = self.xys[mask]
        self.Npts = len(self.xys)
        # Remove the black 'x' overmarks for selected points
        self.selpts.remove()
        self.selpts = None
        # Reset the selection indicies
        self.ind = np.array([],dtype=int)
        # Draw the canvas, resetting the plotting limits first
        set_axis_lim(self.ax, self.data)   
        self.canvas.draw_idle()

    def clear_selected(self):
        self.selpts.remove()
        self.selpts = None
        self.ind = np.array([],dtype=int)
        self.canvas.draw_idle()

#
# Functions to remove selected points
#
def edit_ccdata(ccdata, run_history, color_epochs=True, core_pos=None):
    """
    Edit the clean component data by removing selected points.

    Parameters
    ----------
    ccdata : `~numpy.ndarray`
        Clean component data to be edited.
    color_epochs : bool, optional
        If True, color the epochs in the plot. The default is True.
    core_pos : `~numpy.ndarray`, optional
        Core position to be used for coloring. The default is None.
    PlotFlagged : bool, optional
        If True, plot already Flagged data as well. The default is True.
    ReturnUnflagged : bool, optional
        If True, return only the unflagged data. The default is False.
    Returns
    -------
    ccdata : `~numpy.ndarray`
        Edited clean component data.
    """
    
    # subset to only use unflagged data here
    active_ccdata = ccdata[ccdata['group'] >= 0].copy()

    # Create a scatter plot of the data
    fig, ax, scat = plot_cclist(active_ccdata,color_epochs=color_epochs,
                                   core_pos=core_pos, PlotFlagged=False)

    # Create a "selector" object that has a copy of the data that can be edited
    selector = SelectFromCollection(ax, scat, active_ccdata.copy())

    def accept(event, ccdata=ccdata):
        if event.key == "enter":
            if len(selector.ind) < 1:
                print("No points selected")
            else:
                selector.flag_selected()
                fig.canvas.draw()
        elif event.key == " ":
            print("Clearing selection")
            selector.clear_selected()
            fig.canvas.draw()

    fig.canvas.mpl_connect("key_press_event", accept)
    ax.set_title("Press enter to flag selected points.\nSpace to clear selection. Close window (\'q\') to quit.")
    
    #
    # Use plt.show() to require blocking and prevent closing function before data are ready to return
    #
    plt.show()

    # report what was flagged by the user...
    flagged_points=np.setdiff1d(active_ccdata,selector.data)
    if len(flagged_points) <= 20:
        print("Interactively flagged points:\n",flagged_points)
    else:
        print("Interactively flagged {0} points.\n".format(len(flagged_points)))
    # log it in history file...
    if len(flagged_points) > 0:
        if len(flagged_points) <= 20:
            run_history.append("# Interactively flagged points:\n".format(flagged_points))
            for i in range(len(flagged_points)):
                run_history.append("#  {0}\n".format(flagged_points[i]))
        else:
            run_history.append("# Interactively flagged {0} points.\n".format(len(flagged_points)))

    return selector.data, (len(flagged_points) > 0) 

#------------------------------------------------------ 
# Find_Moving_Clusters
#------------------------------------------------------ 
#  Function designed to find moving clusters using an algorithm similar
#    to KMeans, but including slopes over time.  See description of the
#    main loop below.  
#
#  NOTE: if ClusterType = 'GMM' is selected, the function will
#    assign weights using each points distance from each cluster and the
#    estimated cluster sizes, giving an effective "Gaussian Mixing Model"
#    which does not do a 'hard' clustering model (i.e. points can be part
#    of more than one cluster)
#
#  Input Data has the form of a structured numpy array:
#
#  dtype = {'names':('epoch','x','y','flux','sizex','sizey','group'),
#           formats':('f8','f8','f8','f8','f8','f8','i') })
#
# Output has multiple parts...
#
#   'cluster_data':cluster_data,          # cluster data -- see below
#   'labels':labels,                      # labels for input data
#   'probability':probability,            # probability for input data to each cluster
#   'dist_sqr':best_distance_sqr,             # mean square distance metric (smaller = better)
#   'log_likelihood':best_log_likelihood  # log likelihood metric (large = better)
#   'rms_sep':rms_separations             # RMS (flux-weighted) separation of each point from 
#                                         # its assigned cluster
#            
#    NOTES: 
#    ------
#    (1) cluster_data output is a structured array: 
#
#cluster_datatype = np.dtype(dtype = {'names':('epoch','centX','centY','dcentX','dcentY',
#                                              'slopeX','slopeY','dslopeX','dslopeY',
#                                              'accelX','accelY','daccelX','daccelY','medianFlux',
#                                              'sizeMaj','sizeMin','sizePA','label'),
#                            'formats':('f8','f8','f8','f8','f8','f8','f8','f8',
#                                       'f8','f8','f8','f8','f8',
#                                       'f8','f8','f8','f8','f8','f8','i') })
#
#    (2) labels is a list of labels assigned to the input data in order 
#          (NOTE: label = highest prob. cluster, see next output) [size = data]
#    (3) probability is a list of normalized probabilities for each data 
#        to be associated with each cluster, in data order  [size = data x Nclusters]
#    (4) Only one metric is used to determine convergence, depending on algorithm 
#           KMeans uses mean square distance
#           GMM uses log likelihood
#        But both metrics are always computed and returned.
#
def Find_Moving_Clusters(source, band, ccdata, Nclusters, epoch_info, 
                         max_iter = 200, max_loop=1000, random_step=3,
                         print_info = True, print_diag = False, Fit_Accel = True,
                         ClusterType="KMeans", EGauss=False, StationaryCore=False, 
                         RefEpochType="Middle", # options ["Middle", "Median"]
                         SigmaCut=0.0, input_core_pos = None, input_core_id = None,
                         CoreIDMethod="JetEnd",   # options ["Center", "JetEnd"]
                         JetDir = None,
                         StokesQU_weight = 1e-9,
                         start_guess=None):

    # Define the cluster data type for the starting guess if not provided                     
    if start_guess is None:
        start_guess = np.array([], dtype = cluster_datatype)

    #---------------------------------------------------------------------------
    #
    # Confirm the ClusterType was specified correctly
    if ClusterType not in ["KMeans", "GMM"]:
        print("Unrecognized Cluster Type = {0}".format(ClusterType))
        return 0
    #
    if print_info:
        print("-------------------------------------------------------------------")
        print("Running with ClusterType = {0}, assuming {1} clusters".format(ClusterType, Nclusters))
        print("Starting Guess = {0}".format(start_guess))
        print("-------------------------------------------------------------------")
        
    # define a string for printing information about the fit later during iterations
    metric_type = "mean_distance_sqr"
    if ClusterType == "GMM":
        metric_type = "log_likelihood"

    # check if ccdata has any 'group' < 0 points and return an error if that is
    #  the case.  
    if len(ccdata[ccdata['group'] < 0]) > 0:
        print("Flagged input data found in ccdata!  Abort.")
    
    # make a copy of the data to be sure nothing gets changed    
    data = ccdata.copy()
    
    #---------------------------------------------------------------------------
    #
    # Setup data, result arrays, and starting guesses for the algorithim 
    #
    
    # Create a list of epochs in the data
    epoch_list = np.sort(np.unique(data['epoch']))
    # ref_epoch is either the median epoch or the average of the first and last epoch, 
    #    NOTE that this might not equal a specific epoch.
    if RefEpochType not in ["Median", "Middle"]:
        print("Unrecognized RefEpochType = {0}, using 'Middle'".format(RefEpochType))
        RefEpochType = "Middle"
    if RefEpochType == "Median":
        ref_epoch = np.round(np.median(epoch_list), 4)
    else:  # RefEpochType == "Middle"
        ref_epoch = np.round((epoch_list[0]+epoch_list[-1])/2, 4)
    epochs = len(epoch_list)
    epoch_nearest_ref = epoch_list[np.argsort(np.abs(epoch_list-ref_epoch))]

    # Beam size in FWHM and in sigma units (mas) for use in cluster size calculations
    beam_maj = np.median(epoch_info['bmaj'])
    beam_min = np.median(epoch_info['bmin'])
    beam = np.sqrt(beam_maj*beam_min) #np.sqrt(np.median(epoch_info['bmaj']*epoch_info['bmin']))
    sigma_per_fwhm = (2*np.sqrt(2*np.log(2)))
    beam_maj_sig = beam_maj/sigma_per_fwhm    # 1 sigma in mas
    beam_min_sig = beam_min/sigma_per_fwhm    # 1 sigma in mas
    beam_sig = beam/sigma_per_fwhm            # 1 sigma in mas

    # Pixel size
    pixel_size = np.median(epoch_info['pix_to_mas'])

    # Define minimum cluster sizes for cluster size calculations
    min_cluster_size = beam_sig/5
    min_cluster_size_minor = beam_min_sig/5
    min_cluster_size_major = beam_maj_sig/5
  
    #  Make containers for the moving cluster centers, slopes, and uncertainties
    cl_centX = np.zeros(Nclusters)
    cl_centY = np.zeros(Nclusters)
    cl_slopeX = np.zeros(Nclusters)
    cl_slopeY = np.zeros(Nclusters)
    cl_accelX = np.zeros(Nclusters)
    cl_accelY = np.zeros(Nclusters)
    cl_dslopeX = np.zeros(Nclusters)
    cl_dcentX = np.zeros(Nclusters)
    cl_dslopeY = np.zeros(Nclusters)
    cl_dcentY = np.zeros(Nclusters)
    cl_daccelX = np.zeros(Nclusters)
    cl_daccelY = np.zeros(Nclusters)
    cl_sizes = np.zeros((Nclusters,3))    # each is listed as [major, minor, PA (sky coord)]
    
    # setup tiled versions of the data, with the reference epoch subtracted
    #  --> the idea behind the tiled versions of the data is to allow efficient
    #      matrix calculations with Numpy when finding the distances of each 
    #      point to each possible cluster
    t = np.tile(data['epoch'] - ref_epoch,(Nclusters,1)).T
    x = np.tile(data['x'],(Nclusters,1)).T  
    y = np.tile(data['y'],(Nclusters,1)).T
    f = np.tile(data['flux'],(Nclusters,1)).T
    s = np.tile(data['stokes'],(Nclusters,1)).T
            
    # Adjust for input core positions (may be refined on output if Stationary_Core=True)
    if input_core_pos is not None:
        #print("Making input core correction")
        for i in range(len(epoch_list)):
            epoch_mask = (data['epoch'] == epoch_list[i])
            x[epoch_mask,:] -= input_core_pos['x'][i]
            y[epoch_mask,:] -= input_core_pos['y'][i]

    # setup labels as a list of -1 to start
    labels = -np.ones(len(t[:,0]),dtype=int)

    # setup weight and probability arrays for later use
    weights = np.tile(np.zeros(Nclusters),(len(t[:,0]),1))
    prob = np.tile(np.zeros(Nclusters),(len(t[:,0]),1))

    # metrics to monitor convergence during an iteration
    mean_distance_sqr = 1e30
    mean_distXsqr = 1e30
    mean_distYsqr = 1e30
    log_likelihood = -np.inf

    # Create an initialized random number generator
    rng = np.random.default_rng(2200420)  # fixed seed for reproducibility

    # If we have no starting guess... use clustering on the reference epoch
    #   to get one for the centers...  leave slopes at 0
    if len(start_guess['epoch']) == 0:
    
        # start by finding the cluster centers at all epochs combined 
        #   --> use data corrected by best estimate of core position
        #   --> weights are the fluxes of those same points
        #   --> Set sample weight to ABS of flux to accomodate q, u fluxes 
        data_to_fit = np.vstack((x[:,0],y[:,0])).T
        weights = np.abs(f)
        weights[s != 'i'] *= StokesQU_weight
        n_init = int(np.ceil(2.0+np.log(len(data_to_fit))))   # kmeans++ optimimum randomization trials
        Results = cluster.KMeans(n_clusters=Nclusters,n_init=n_init,random_state=128554).fit(data_to_fit,sample_weight=np.array(weights[:,0]))

        # Define initial cluster centers and slope... use the KMeans results
        #  for the original centers and set the slopes to 0 to start
        cl_centX = np.around(Results.cluster_centers_[:,0],5)
        cl_centY = np.around(Results.cluster_centers_[:,1],5)
        labels = Results.labels_
        
        # Assign the core cluster -- choice made once!
        core_cl = None   # will be assigned below...
        
        # Only use clusters present in all epochs with at least 1% of overall flux
        clmask = np.zeros(Nclusters,dtype='bool')   # default to False
        for i in range(0,Nclusters):
            if np.sum(weights[labels==i]) >= 0.01*np.sum(weights) and\
                len(np.unique(data[labels==i]['epoch'])) == epochs:
                clmask[i] = True

        # Only update if there is more than one cluster present in all epochs
        if np.sum(clmask) > 1:
            # if we have a Jet Direction defined, project positions along that direction
            #.  for purposes of locating the core
            if JetDir is not None:
                proj = cl_centX[clmask]*np.sin(JetDir*np.pi/180.0)+cl_centY[clmask]*np.cos(JetDir*np.pi/180.0)
                cl_X = proj*np.sin(JetDir*np.pi/180.0)
                cl_Y = proj*np.cos(JetDir*np.pi/180.0)
            else:
                cl_X = cl_centX[clmask]
                cl_Y = cl_centY[clmask]
            if CoreIDMethod == "Center":
                core = np.argmin(cl_X**2+cl_Y**2)
                core_cl = np.argwhere((cl_centX == cl_centX[clmask][core]) & (cl_centY == cl_centY[clmask][core]))[0,0]
            elif CoreIDMethod == "JetEnd":
                # Leverage Jet Direction if we have it
                if JetDir is not None:
                    core = np.argmin(proj)
                else:
                    far_cl = np.argmax(cl_X**2+cl_Y**2)
                    core = np.argmax((cl_X-cl_X[far_cl])**2+(cl_Y-cl_Y[far_cl])**2)
                core_cl = np.argwhere((cl_centX == cl_centX[clmask][core]) & (cl_centY ==cl_centY[clmask][core]))[0,0]
            else:
                print("Need a valid CoreIDMethod, options: Center, JetEnd\n")
                return None

        # Sort them by distance
        if core_cl is not None:
            cl_dist = (cl_centX-cl_centX[core_cl])**2+(cl_centY-cl_centY[core_cl])**2
        else:
            cl_dist = (cl_centX)**2+(cl_centY)**2
            
        cl_centX = cl_centX[np.argsort(cl_dist)]
        cl_centY = cl_centY[np.argsort(cl_dist)]
        
        # reset cluster labels after sorting based on distance from core
        core_cl = 0   # reset core cluster to 0
        labels = -1   # reset labels after making initial guess due to resort 
    
        # print some info...
        if print_diag:
            print("Sklearn KMeans returns initial cluster centers (X,Y) at reference epoch:")
            print(" --> picking cluster {0} as the core".format(core_cl))
            for i in range(Nclusters):
                print("[{0:8.5f}, {1:8.5f}]".format(cl_centX[i],cl_centY[i]))
        elif input_core_id is not None:
            print("Warning: input_core_id ignored, assigned to {0} by starting KMeans guess".format(core_cl))    
        
    else:
        if start_guess['epoch'][0] == ref_epoch and len(start_guess['epoch']) == Nclusters:

            # copy the values from our starting guess                                                 
            cl_centX = np.copy(start_guess['centX'])
            cl_centY = np.copy(start_guess['centY'])
            cl_slopeX = np.copy(start_guess['slopeX'])
            cl_slopeY = np.copy(start_guess['slopeY'])
            cl_accelX = np.copy(start_guess['accelX'])
            cl_accelY = np.copy(start_guess['accelY'])
 
            # Core cluster may have been assigned by input_core_id above
            #  --> if not, assume it is the first in the guess
            if input_core_id is None:
                core_cl = 0
            else:
                core_cl = input_core_id
            
        else:
            print("Starting guess does not match reference epoch or too few clusters!")
            return None

    #---------------------------------------------------------------------------    
    # setup saved versions of moving cluster data
    #   to facilitate multiple iterations 
    #
    best_distance_sqr = 1e30
    best_log_likelihood = -np.inf
    best_iteration = 0
    saved_cl_centX = np.copy(cl_centX)
    saved_cl_centY = np.copy(cl_centY)
    saved_cl_slopeX = np.copy(cl_slopeX)
    saved_cl_slopeY = np.copy(cl_slopeY)
    saved_cl_accelX = np.copy(cl_accelX)
    saved_cl_accelY = np.copy(cl_accelY)
    saved_cl_dslopeX = np.copy(cl_dslopeX)
    saved_cl_dslopeY = np.copy(cl_dslopeY)
    saved_cl_daccelX = np.copy(cl_daccelX)
    saved_cl_daccelY = np.copy(cl_daccelY)
    saved_cl_dcentX = np.copy(cl_dcentX)
    saved_cl_dcentY = np.copy(cl_dcentY)
    saved_cl_sizes = np.copy(cl_sizes)
    saved_labels = np.copy(labels)
    saved_weights = np.copy(weights)
    saved_prob = np.copy(prob)

    
    #---------------------------------------------------------------------------
    #
    # After the initial guesses given above...
    #  --> loop over many iterations of moving cluster finding loop
    #        with updated starting points.
    #  --> First few iterations have progressively more epochs included,
    #        see below for details
    #  --> Remaining iterations use randomized starting guesses based
    #        on the best results found so far
    #   
    
    # Define a tolerance factor for convergence from one loop 
    #   to the next.  Same tolerance is used in both outer and inner loop
    converg_tol = 1e-6
    
    # Initialize a count for consecutive iterations with no change
    no_improve_count = 0
    no_improve_limit = 20  # limit at which we abort trying to improve
    
    for k in range(max_iter):
        # Print out an iteration indicator if we are taking close look
        if print_diag:
            print("-- iteration k = {0} --".format(k))
        #
        # Initialize starting values for this iteration, including updating
        #   core offset, first-epoch information, and randomizing starting
        #   values.
        #
        #   the best result from the previous iteration if it improved 
        #   our overall fit metric.   
        #
        #  --> Only take these steps once we are past the iteration where
        #      we might be including new epochs in the list!!
        #
        if k > 0 and (k > len(epoch_list)-4 or len(start_guess['epoch']) > 0):
            #
            # If the previous round was an improvement, update core and first epoch info
            #
            if best_iteration == k-1:
                #
                # For a stationary core assumption, adjust positions for core cluster in each epoch
                # 
                if StationaryCore:
                    #
                    # if coreIDMethod == 'Center', first see if things have shifted enough that we need
                    #    a coreID change
                    # 
                    if CoreIDMethod == "Center" and input_core_pos is not None:
                        # compute offsets for each epoch based on core location updates done so far ...
                        test_core_pos = np.zeros(len(epoch_list),dtype=corepos_datatype)
                        test_flux_sum = np.zeros(epochs)
                        for i in range(len(epoch_list)):
                            epoch_mask = (data['epoch'] == epoch_list[i])
                            test_flux_sum[i] = np.sum(f[epoch_mask,0])
                            test_core_pos['x'][i] = np.sum(f[epoch_mask,0]*(data['x'][epoch_mask] - x[epoch_mask,0]))/test_flux_sum[i]
                            test_core_pos['y'][i] = np.sum(f[epoch_mask,0]*(data['y'][epoch_mask] - y[epoch_mask,0]))/test_flux_sum[i]
                        # find which cluster is closest to the original input_core_pos across epoch
                        test_cl_centX_diff = cl_centX + np.sum(test_flux_sum*test_core_pos['x'])/np.sum(test_flux_sum)\
                                                        - np.sum(test_flux_sum*input_core_pos['x'])/np.sum(test_flux_sum)
                        test_cl_centY_diff = cl_centY + np.sum(test_flux_sum*test_core_pos['y'])/np.sum(test_flux_sum)\
                                                        - np.sum(test_flux_sum*input_core_pos['y'])/np.sum(test_flux_sum)
                        test_cl_dist_sqr = test_cl_centX_diff**2+test_cl_centY_diff**2
                        # change core 
                        save_core_cl = core_cl
                        core_cl = np.argmin(test_cl_dist_sqr)
                        if core_cl != save_core_cl:
                            print("Changed core_cl from {0} to {1}".format(save_core_cl,core_cl))

                    #
                    # Adjust positions in each epoch to enforce stationary core
                    #
                    cx = np.zeros(epochs)
                    cy = np.zeros(epochs)
                    sum_weights = np.zeros(epochs)
                    for i in range(epochs):
                        epoch_mask = (data['epoch'] == epoch_list[i])
                        sum_weights[i] = np.sum(weights[epoch_mask,core_cl])
                        if sum_weights[i] > 0:
                            cx[i] = np.sum(weights[epoch_mask,core_cl]*x[epoch_mask,0])/sum_weights[i]
                            cy[i] = np.sum(weights[epoch_mask,core_cl]*y[epoch_mask,0])/sum_weights[i]
                            x[epoch_mask,:] -= cx[i] 
                            y[epoch_mask,:] -= cy[i] 
                        else:
                            sum_weights[i] = 0
                    if np.sum(sum_weights > 0):
                        mean_cx = np.sum(sum_weights*cx)/np.sum(sum_weights)
                        mean_cy = np.sum(sum_weights*cy)/np.sum(sum_weights)
                        # Adjust cluster properties appropriately
                        cl_centX -= mean_cx
                        cl_centY -= mean_cy
                        saved_cl_centX = cl_centX
                        saved_cl_centY = cl_centY
      
            #
            #  Randomize our starting guess for this iteration
            #
            cl_centX = rng.normal(loc=saved_cl_centX,scale=random_step*(0.02*np.abs(saved_cl_centX)+0.03*beam))
            cl_centY = rng.normal(loc=saved_cl_centY,scale=random_step*(0.02*np.abs(saved_cl_centY)+0.03*beam))
            cl_slopeX = rng.normal(loc=saved_cl_slopeX,scale=random_step*(0.02*np.abs(saved_cl_slopeX)))
            cl_slopeY = rng.normal(loc=saved_cl_slopeY,scale=random_step*(0.02*np.abs(saved_cl_slopeY)))
            # fix accel parameters -- copy, not by reference!
            cl_accelX = saved_cl_accelX.copy()
            cl_accelY = saved_cl_accelY.copy()
            # fix core_cl properties if StationaryCore 
            if StationaryCore:
                cl_centX[core_cl] = saved_cl_centX[core_cl]
                cl_centY[core_cl] = saved_cl_centY[core_cl]
                cl_slopeX[core_cl] = saved_cl_slopeX[core_cl]
                cl_slopeY[core_cl] = saved_cl_slopeY[core_cl]
                cl_accelX[core_cl] = saved_cl_accelX[core_cl]
                cl_accelY[core_cl] = saved_cl_accelY[core_cl]

        # Now execute the main loop where we...
        #    (1) Take the current best centers and slopes and calculate the distance
        #        from each data point to each possible cluster center, corrected 
        #        for the motion of that center over time (given by the slope variables)
        #    (2) For each data point, we find the cluster with the smallest distance
        #        and that cluster becomes identified with that data point.  The
        #        identities of each data point are stored in the 'labels' variable.
        #    (3) Assign weights to each datapoint to each cluster.
        #        If ClusterType = "KMeans" --> Hard Clustering
        #           Use labels defined in step (2) to set weights = flux*1.0 for the
        #           cluster matching the label and weight = 0.0 for the other clusters
        #        If ClusterType = "GMM" --> Soft Clustering
        #           Use labels only to help us get started (first iteration). Otherwise
        #           update weights from the previous iteration by calculating a probability
        #           that a point is associated with each cluster and then scale
        #           the total probability for that point so that sum(weights) over
        #           clusters = flux*1.0 for that datapoint. 
        #    (4) All of the data points over time are then used to calculate new 
        #        cluster centers and slopes using the weights defined in step (3)
        #    (5) Steps above are repeated until we converge on a solution.
        #
        
        # reset metrics to study convergence for the next loop step
        mean_distance_sqr = 1e30
        mean_distXsqr = 1e30
        mean_distYsqr = 1e30
        log_likelihood = -np.inf
        iteration_converged = True

        for j in range(max_loop):
            #------------------------------------------------------------------------------        
            # Create tiled versions of the center and slope variables to allow efficient
            #  calculation of the distance to each cluster using Numpy array arithmetic
            centX = np.tile(cl_centX,(len(t[:,0]),1))
            centY = np.tile(cl_centY,(len(t[:,0]),1))
            slopeX = np.tile(cl_slopeX,(len(t[:,0]),1))
            slopeY = np.tile(cl_slopeY,(len(t[:,0]),1))
            accelX = np.tile(cl_accelX,(len(t[:,0]),1))
            accelY = np.tile(cl_accelY,(len(t[:,0]),1))
 
            #------------------------------------------------------------------------------
            # construct a matrix of distances from all the possible clusters, correcting for the
            #  current best slope for those clusters
            #
            # First calcuate distance squared values, 
            #   These will also be used in cluster sizes estimates below, so compute
            #   cross-correlation as well...
            distXsqr = (x-centX-slopeX*t-0.5*accelX*t**2)**2
            distYsqr = (y-centY-slopeY*t-0.5*accelY*t**2)**2
            distXY = (x-centX-slopeX*t-0.5*accelX*t**2)*(y-centY-slopeY*t-0.5*accelY*t**2)
            
            # Combine to get total distance_squared
            distances_sqr = distXsqr+distYsqr 
            
            # Assign cluster labels to points based on their minimum distance
            # --> "hard" clustering, but note the weight assignment below 
            #  can allow each point to contribute to mulitple clusters if ClusterType = "GMM"
            # --> labels will not affect "GMM", only "KMeans".  In "GMM" the weights are
            #  revised in each round and kept for the next round
            labels = np.argmin(distances_sqr,axis=1)

            #------------------------------------------------------------------------------
            # On our first pass through OR if we are doing KMeans (hard clustering)
            #   create a weights array with them all equal to flux*1.0 for points 
            #   assigned to a particular cluster label, or 0 otherwise
            if j==0 or ClusterType == "KMeans":
                weights = np.abs(f)
                weights[s != 'i'] *= StokesQU_weight 
                # set every weight not labelled with this cluster = 0
                for i in range(Nclusters):
                    weights[labels != i, i] = 0.0 

            #------------------------------------------------------------------------------
            # if there was no starting parameter guess, use the first few iterations
            #  to progressively include the closest epochs first by setting weights
            #  for those other epochs = 0
            if k < len(epoch_list)-4 and len(start_guess['epoch']) == 0:
                allowed_time_gap = np.abs(epoch_nearest_ref[k+3]-ref_epoch)
                weights[np.abs(t[:,0]) > allowed_time_gap+0.0001,:] = 0.0

            #------------------------------------------------------------------------------------                    
            # Compute the standard deviation to estimate the size for each cluster independently 
            #
            #  --> Now has grown into a full calculation of the sigma-squared matrix which includes possible
            #      correlations between x and y positions (due to an elliptical gaussian distribution)

            # Default variance correlation matrix values are for the beam size
            varX = np.tile(beam_min_sig**2,Nclusters)   # default to beam size
            varY = np.tile(beam_maj_sig**2,Nclusters)
            varXY = np.tile(0.0,Nclusters)
            # calculate values from scatter in data only if we have at least 10 valid points
            for i in range(Nclusters):
                nvals = np.sum(weights[:,i] > 0.1*np.abs(f[:,0]))
                if nvals >= 10:
                    varX[i] = (nvals/(nvals-1.0))*np.sum(weights[:,i]*distXsqr[:,i])/np.sum(weights[:,i])
                    varY[i] = (nvals/(nvals-1.0))*np.sum(weights[:,i]*distYsqr[:,i])/np.sum(weights[:,i])
                    varXY[i]= (nvals/(nvals-1.0))*np.sum(weights[:,i]*distXY[:,i])/np.sum(weights[:,i])
                    # don't find sizes that are implausibly small
                    if varX[i] < (min_cluster_size_minor)**2:
                        varX[i] = (min_cluster_size_minor)**2
                    if varY[i] < (min_cluster_size_major)**2:
                        varY[i] = (min_cluster_size_major)**2
                    # Check if this value will give a very small DETvar or nvals is too low or EGauss is turned off
                    #   if so, make it a circle 
                    if varXY[i]**2 > varX[i]*varY[i]-(min_cluster_size)**4 or nvals < 20 or EGauss==False:
                        varX[i] = (varX[i]+varY[i])/2
                        varY[i] = varX[i]
                        varXY[i] = 0.0
            # Calculate the determinant
            DETvar = varX*varY-varXY**2
            # Abort if we detect an issue with a negative or nan determinant
            if np.any(DETvar <= 0) or np.any(np.isnan(DETvar)):
                print("Found a DETvar issue:")
                print(DETvar)
                print(varX)
                print(varY)
                print(varXY)
                return 0
            # Assign the cluster size/shape in *SKY coordinates* in terms of the position angle
            for i in range(Nclusters):
                # Define correlation matrix 
                corMatrix = np.array([[varX[i], varXY[i]],[varXY[i], varY[i]]])
                # Calculate eigenvalues and eigenvectors
                evalue, evector = np.linalg.eig(corMatrix)
                # compute Major and minor axis
                maj_index = np.argmax(evalue)
                min_index = np.argmin(evalue)
                maj_axis = np.sqrt(evalue[maj_index])
                min_axis = np.sqrt(evalue[min_index])
                # Use the eigenvector components of the major axis to find its position angle
                # *SKY* coordinate position angle which is relative to Y-axis in DEGREES!!
                pa = (180/np.pi)*np.arctan2(evector[0][maj_index],evector[1][maj_index])
                # adjust pa to be > -90 and <= 90
                if pa <= -90:  
                    pa = pa + 180
                elif pa > 90:
                    pa = pa - 180
                # Record these parameters as the cluster size
                cl_sizes[i] = np.array([maj_axis, min_axis, pa])
            
            #------------------------------------------------------------------------------
            #  Compute a probability based on distance of each point from each 
            #   cluster.  This is effectively a Gaussian mixture model, but compute
            #   these probabilities for both ClusterTypes for a comparison later
            #
            #   NOTE only for ClusterType == "GMM" do the probabilities or log-likelihood
            #    affect convergence and the weights of the data
            #
            pos_unc = (min_cluster_size)**2 #(beam/5.0)**2 # rough estimate of position uncert. of clean comp.
            #dist_prob = (pos_unc/(2*np.pi*np.sqrt(DETvar))) \
            #        *np.exp(-0.5*(1.0/DETvar)*(distXsqr*varY + distYsqr*varX - 2*distXY*varXY))               
            log_dist_prob = np.log(pos_unc)-np.log(2*np.pi*np.sqrt(DETvar))\
                -0.5*(1.0/DETvar)*(distXsqr*varY + distYsqr*varX - 2*distXY*varXY)

            # If we are several iterations in *and* past the point where we are incorporating new epochs...
            #  --> Do flagging based on features that have a distance probability 
            #      of being part of *any* cluster less than SigmaCut
            if j > 4 and (k > len(epoch_list)-4 or len(start_guess['epoch']) > 0) and SigmaCut > 0:
                # Calculate the number of sigma from its assigned cluster:
                sigma_diff = np.sqrt((1.0/DETvar)*(distXsqr*varY + distYsqr*varX - 2*distXY*varXY))
                index_array = np.arange(0,len(sigma_diff[:,0]),1)
                #print(sigma_diff[index_array,labels].shape)
                # Never flag more than 3 percent of the data...
                cut_level = np.max([SigmaCut, np.percentile(sigma_diff[index_array,labels],97)])
                # 
                flag_vals = np.nonzero(sigma_diff[index_array,labels] > cut_level)    # argwhere?
                #print(flag_vals)
                labels[flag_vals] = -1
                log_dist_prob[flag_vals,:] = 1.0
                weights[flag_vals,:] = 0.0

            #
            # The above probabilities are based only on distance of the component and size of cluster
            #  --> we should also incorporate the cluster flux as different clusters
            #      are more probable based on their flux
            flux_prob = np.ones(log_dist_prob.shape)*(np.sum(weights, axis=0)/np.sum(weights))
                     
            prob = np.exp(log_dist_prob)*flux_prob
                              
            if ClusterType == "GMM":  
                # Re-compute weights such that they are scaled so that the total weight for each 
                # component is = flux when summed across all of the clusters.
                norm_factor = np.tile(np.sum(prob,axis=1),(Nclusters,1)).T   # don't forget to transpose
                # cases where prob = 0 across all clusters will give a weight of zero, but
                #.  their normalization factor should be 1 to avoid dividing by zero
                norm_factor[norm_factor == 0] = 1
                weights = np.abs(f)*prob/norm_factor
                weights[s != 'i'] *= StokesQU_weight

                # Apply any flagged data
                weights[labels == -1,:] = 0.0
                
                # if there was no starting parameter guess, use the first few iterations
                #  to progressively include the closest epochs first by setting weights
                #  for those other epochs = 0
                if k < len(epoch_list)-4 and len(start_guess['epoch']) == 0:
                    allowed_time_gap = np.abs(epoch_nearest_ref[k+3]-ref_epoch)
                    weights[np.abs(t[:,0]) > allowed_time_gap+0.0001,:] = 0.0

            #------------------------------------------------------------------------------
            # Compute metrics to study this iteration for convergence
            #
            # compute log_likelihood
            last_log_likelihood = log_likelihood
            if ClusterType == 'KMeans':
                prob_temp = prob.copy()
                for i in range(Nclusters):
                    prob_temp[labels != i, i] = 0.0
                prob_sum_across_clust = np.sum(prob_temp, axis=1)
            else:   
                prob_sum_across_clust = np.sum(prob, axis=1)

            # Due to flagging, we may have some prob_sum_across_clust = 0 and we cannot
            #. include those in the log likelihood calculation
            prob_sum_across_clust[prob_sum_across_clust <= 0] = 1e-30   # avoid log(0) issues
            # --> must include data weights at this point, consider relative to mean Stokes I comp.
            data_weights = np.sum(weights,axis=1)
            #
            # compute weighted mean log likelihood
            log_likelihood = np.sum(np.log(prob_sum_across_clust)*data_weights)/np.sum(data_weights) 

            # compute mean distance square    
            last_distance_sqr = mean_distance_sqr
            mean_distXsqr = np.sum(weights*distXsqr)/np.sum(weights)
            mean_distYsqr = np.sum(weights*distYsqr)/np.sum(weights)
            mean_distance_sqr = np.sum(weights*distances_sqr)/np.sum(weights)

            # Print some diagnostics on the log-likelihood and distance_sqr metrics
            if print_diag:
                print("{0:4d}, logP={1:0.9f}, distSQR={2:0.9f}".format(j,log_likelihood,mean_distance_sqr))
                      
            # Check for convergence based on log-likelihood for GMM or mean_distance_sqr for KMeans 
            if j > 5 and ClusterType == "KMeans":          
                if np.abs(last_distance_sqr-mean_distance_sqr) <= converg_tol*mean_distance_sqr:
                    break
            elif j > 5 and ClusterType == "GMM":
                if np.abs(last_log_likelihood-log_likelihood) <= converg_tol*np.abs(log_likelihood):
                    break
                                    
            #------------------------------------------------------------------------------            
            # Find new slopes and new centers for each cluster 
            # --> only if we still have steps left in this loop
            if j < max_loop-1:
                for i in range(Nclusters):
                    # --------------------------------------
                    # Find motion and position for cluster
                    # --------------------------------------
                    # must have at least five unique timestamps in data that have weights > 0.1*flux
                    ntimes = len(np.unique(t[weights[:,i] > 0.1*np.abs(f[:,i]),0]))
                    npoints = np.sum(weights[:,i] > 0.1*np.abs(f[:,i]))
                    #
                    if ntimes > 4:
                        #
                        # Only fit a slope if this is not the core OR we do not require a stationary core
                        #
                        if (not(StationaryCore) or core_cl != i):
                            if ntimes > 9 and Fit_Accel:
                                deg_fit = 2
                            else:
                                deg_fit = 1
                            #
                            # Do the fit.
                            #
                            px, corrx = np.polyfit(t[:,0],x[:,0],
                                                   deg=deg_fit,cov=True,w=np.sqrt(weights[:,i]))
                            py, corry = np.polyfit(t[:,0],y[:,0],
                                                   deg=deg_fit,cov=True,w=np.sqrt(weights[:,i]))
                            #
                            # If this was an accel. fit *and* it was significant at >= 3sigma, assign the accel.
                            #
                            if deg_fit == 2 and\
                                (px[0]**2+py[0]**2)/np.sqrt(px[0]**2*corrx[0][0]+py[0]**2*corry[0][0]) >= 3.0:
                            
                                cl_accelX[i] = px[0]
                                cl_daccelX[i] = np.sqrt(corrx[0][0])
                                cl_slopeX[i] = px[1]
                                cl_dslopeX[i] = np.sqrt(corrx[1][1])
                                cl_centX[i] = px[2]
                                cl_dcentX[i] = np.sqrt(corrx[2][2])
        
                                cl_accelY[i] = py[0]
                                cl_daccelY[i] = np.sqrt(corry[0][0])
                                cl_slopeY[i] = py[1]
                                cl_dslopeY[i] = np.sqrt(corry[1][1])
                                cl_centY[i] = py[2]
                                cl_dcentY[i] = np.sqrt(corry[2][2])

                            else:
                                # redo fit if needed
                                if deg_fit == 2:
                                    px, corrx = np.polyfit(t[:,0],x[:,0],
                                                       deg=1,cov=True,w=np.sqrt(weights[:,i]))
                                    py, corry = np.polyfit(t[:,0],y[:,0],
                                                       deg=1,cov=True,w=np.sqrt(weights[:,i]))

                                # Apply fit if overall slope is significant
                                if (px[0]**2+py[0]**2)/np.sqrt(px[0]**2*corrx[0][0]+py[0]**2*corry[0][0]) >= 3.0:

                                    cl_accelX[i] = 0
                                    cl_daccelX[i] = 0
                                    cl_slopeX[i] = px[0]
                                    cl_dslopeX[i] = np.sqrt(corrx[0][0])
                                    cl_centX[i] = px[1]
                                    cl_dcentX[i] = np.sqrt(corrx[1][1])
                
                                    cl_accelY[i] = 0
                                    cl_daccelY[i] = 0
                                    cl_slopeY[i] = py[0]
                                    cl_dslopeY[i] = np.sqrt(corry[0][0])
                                    cl_centY[i] = py[1]
                                    cl_dcentY[i] = np.sqrt(corry[1][1]) 

                                # otherwise just go with average positions
                                else:
                                    cl_centX[i] = np.sum(weights[:,i]*x[:,0])/np.sum(weights[:,i])
                                    cl_dcentX[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(x[:,0]-cl_centX[i])**2)/np.sum(weights[:,i])**2)
                                    cl_centY[i] = np.sum(weights[:,i]*y[:,0])/np.sum(weights[:,i])
                                    cl_dcentY[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(y[:,0]-cl_centY[i])**2)/np.sum(weights[:,i])**2)
                                    
                                    cl_accelX[i] = 0
                                    cl_daccelX[i] = 0
                                    cl_accelY[i] = 0
                                    cl_daccelY[i] = 0
                                    cl_slopeX[i] = 0
                                    cl_dslopeX[i] = 0
                                    cl_slopeY[i] = 0
                                    cl_dslopeY[i] = 0

                        else:
                            cl_centX[i] = np.sum(weights[:,i]*x[:,0])/np.sum(weights[:,i])
                            cl_dcentX[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(x[:,0]-cl_centX[i])**2)/np.sum(weights[:,i])**2)
                            cl_centY[i] = np.sum(weights[:,i]*y[:,0])/np.sum(weights[:,i])
                            cl_dcentY[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(y[:,0]-cl_centY[i])**2)/np.sum(weights[:,i])**2)
                            
                            cl_accelX[i] = 0
                            cl_daccelX[i] = 0
                            cl_accelY[i] = 0
                            cl_daccelY[i] = 0
                            cl_slopeX[i] = 0
                            cl_dslopeX[i] = 0
                            cl_slopeY[i] = 0
                            cl_dslopeY[i] = 0

                    #
                    # OR zero the slopes if we don't have enough unique times to fit for them
                    #
                    else:
                        if npoints > 1:
                            cl_centX[i] = np.sum(weights[:,i]*x[:,0])/np.sum(weights[:,i])
                            cl_dcentX[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(x[:,0]-cl_centX[i])**2)/np.sum(weights[:,i])**2)
                            cl_centY[i] = np.sum(weights[:,i]*y[:,0])/np.sum(weights[:,i])
                            cl_dcentY[i] = np.sqrt(npoints/(npoints-1))*np.sqrt(np.sum(weights[:,i]**2*(y[:,0]-cl_centY[i])**2)/np.sum(weights[:,i])**2)
                            
                            cl_accelX[i] = 0
                            cl_daccelX[i] = 0
                            cl_accelY[i] = 0
                            cl_daccelY[i] = 0
                            cl_slopeX[i] = 0
                            cl_dslopeX[i] = 0
                            cl_slopeY[i] = 0
                            cl_dslopeY[i] = 0
            
                        # NOTE: If none of the above conditions are met, keep the values as they
                        #       were prior to this iteration... make no changes!

            #------------------------------------------------------------------------------
            # Otherwise we have reached the iteration limit without converging
            else:
                iteration_converged = False
                if print_info:
                    print("Iteration {0} did not converge after {1} steps".format(k,max_loop))
                    

            #------------- End of "j" inner loop --------------------------------------------

        # ------------- Final steps of "k" outer loop ---------------------------------        
        #
        #   Update the best result from this iteration if it improved 
        #   our overall fit metric.  
        #
        #   Also check if we might have reached the no-improvement limit
        #
        #  --> Only take these steps once we are past the iteration where
        #      we might be including new epochs in the list!!
        #
        if k >= len(epoch_list)-4 or len(start_guess['epoch']) > 0:
            #
            #  Save results if this is an improvement...
            #    Be sure to copy saved arrays, not associate by reference!!
            #
            if (ClusterType == "KMeans" and mean_distance_sqr < best_distance_sqr) or \
               (ClusterType == "GMM" and log_likelihood > best_log_likelihood):
                # Copy values we want to save
                best_distance_sqr = mean_distance_sqr
                save_mean_distXsqr = mean_distXsqr
                save_mean_distYsqr = mean_distYsqr
                best_log_likelihood = log_likelihood
                saved_cl_centX = np.copy(cl_centX)
                saved_cl_centY = np.copy(cl_centY)
                saved_cl_slopeX = np.copy(cl_slopeX)
                saved_cl_slopeY = np.copy(cl_slopeY)
                saved_cl_accelX = np.copy(cl_accelX)
                saved_cl_accelY = np.copy(cl_accelY)
                saved_cl_dslopeX = np.copy(cl_dslopeX)
                saved_cl_dslopeY = np.copy(cl_dslopeY)
                saved_cl_daccelX = np.copy(cl_daccelX)
                saved_cl_daccelY = np.copy(cl_daccelY)
                saved_cl_dcentX = np.copy(cl_dcentX)
                saved_cl_dcentY = np.copy(cl_dcentY)
                saved_cl_sizes = np.copy(cl_sizes)
                saved_labels = np.copy(labels)
                saved_weights = np.copy(weights)
                saved_prob = np.copy(prob)
                best_iteration = k
                
                # Report improvement if we want...
                if print_info:
                    # Print a warning if the iteration did not converge but still gave an improvement
                    if not(iteration_converged):
                        print("Iteration {0} improved metric ({1}) but did not converge!".format(best_iteration, 
                                                                                                 metric_type))
                        print("  log_likelihood = {0}, mean_distance_sqr = {1}".format(best_log_likelihood,
                                                                                       best_distance_sqr))
                    else:
                        print("Improved {0} metric ({1}) found at iteration {2}".format(ClusterType, metric_type,
                                                                                        best_iteration))
                        print("  log_likelihood = {0}, mean_distance_sqr = {1}".format(best_log_likelihood,
                                                                                       best_distance_sqr))    
            #
            # Check whether it might be OK stop if we've had a few
            #   iterations with no improvement in the results
            #
            if best_iteration != k:
                no_improve_count = no_improve_count+1
                if no_improve_count >= no_improve_limit:
                    break                
            elif best_iteration == k:
                no_improve_count = 0


    #---------------------------------------------------------------------------
    # Sort moving clusters found based on distance from origin 
    #
    cl_distance = np.sqrt(saved_cl_centX**2+saved_cl_centY**2)
    new_labels = np.argsort(cl_distance)
    
    cl_centX = saved_cl_centX[new_labels]
    cl_centY = saved_cl_centY[new_labels]
    cl_slopeX = saved_cl_slopeX[new_labels]
    cl_slopeY = saved_cl_slopeY[new_labels]
    cl_accelX = saved_cl_accelX[new_labels]
    cl_accelY = saved_cl_accelY[new_labels]
    cl_dslopeX = saved_cl_dslopeX[new_labels]
    cl_dslopeY = saved_cl_dslopeY[new_labels]
    cl_daccelX = saved_cl_daccelX[new_labels]
    cl_daccelY = saved_cl_daccelY[new_labels]
    cl_dcentX = saved_cl_dcentX[new_labels]
    cl_dcentY = saved_cl_dcentY[new_labels]
    cl_sizes = saved_cl_sizes[new_labels]
    weights = saved_weights[:,new_labels]
    prob = saved_prob[:,new_labels]
    
    # compute offsets for each epoch based on core location updates done above...
    core_pos = np.zeros(len(epoch_list),dtype=corepos_datatype)
    for i in range(len(epoch_list)):
        epoch_mask = (data['epoch'] == epoch_list[i])
        flux_sum = np.sum(f[epoch_mask,0])
        core_pos['x'][i] = np.sum(f[epoch_mask,0]*(data['x'][epoch_mask] - x[epoch_mask,0]))/flux_sum
        core_pos['y'][i] = np.sum(f[epoch_mask,0]*(data['y'][epoch_mask] - y[epoch_mask,0]))/flux_sum
            
    #
    # compute normalized probabilities each component is associated with each cluster
    #
    # Re-compute probability such that they are scaled so that the total prob for each 
    # component is = 1 when summed across all of the clusters.
    norm_factor = np.tile(np.sum(prob,axis=1),(Nclusters,1)).T   # don't forget to transpose
    # cases where prob = 0 across all clusters will give a weight of zero, but
    #.  their normalization factor should be 1 to avoid dividing by zero
    norm_factor[norm_factor == 0.0] = 1
    probability = prob/norm_factor

    # assign probvals based on weights... this will make it dependent on 
    #  clustering method, hard or soft, which is useful for adding up
    #  fluxes, etc.
    probvals = weights/np.abs(f)
    if StokesQU_weight > 0:
        probvals[s != 'i'] /= StokesQU_weight
    
    # Re-assign clusterID labels based on weights  
    labels = np.argmax(weights,axis=1)
    labels[np.sum(weights,axis=1) == 0.0] = -1

    #---------------------------------------------------------------------------
    # Create a structure with cluster data in it to return:
    #
    #   --> same as cluster_datatype defined above
    # cluster_datatype = np.dtype(dtype = {'names':('epoch','centX','centY','dcentX','dcentY',
    #                                          'slopeX','slopeY','dslopeX','dslopeY',
    #                                          'accelX','accelY','daccelX','daccelY','medianFlux',
    #                                          'sizeMaj','sizeMin','sizePA','label'),
    #                        'formats':('f8','f8','f8','f8','f8','f8','f8','f8',
    #                                   'f8','f8','f8','f8','f8','f8','f8','f8',
    #                                   'f8','i') })
    #
    cluster_data = np.zeros(Nclusters,dtype=cluster_datatype)
    cluster_data['epoch'] = np.tile(ref_epoch,(Nclusters))
    #cluster_data['medianFlux'] = ...
    # --> assign 'Flux' later
    cluster_data['centX'] = cl_centX
    cluster_data['centY'] = cl_centY
    cluster_data['dcentX'] = cl_dcentX
    cluster_data['dcentY'] = cl_dcentY
    cluster_data['slopeX'] = cl_slopeX
    cluster_data['slopeY'] = cl_slopeY
    cluster_data['dslopeX'] = cl_dslopeX
    cluster_data['dslopeY'] = cl_dslopeY
    cluster_data['accelX'] = cl_accelX
    cluster_data['accelY'] = cl_accelY
    cluster_data['daccelX'] = cl_daccelX
    cluster_data['daccelY'] = cl_daccelY
    cluster_data['sizeMaj'] = cl_sizes[:,0]
    cluster_data['sizeMin'] = cl_sizes[:,1]
    cluster_data['sizePA'] = cl_sizes[:,2]
    cluster_data['label'] = np.argsort(np.sqrt(cl_centX**2+cl_centY**2))
 
    #---------------------------------------------------------------------------
    # Print out the results of the fitting in a human readable format...
    #
    if print_info:
        print("-------------------------------------------------------------------")
        print("Best {0} metric ({1}) found at iteration {2}".format(ClusterType, metric_type, best_iteration))
        print("  log_likelihood = {0}, mean_distance_sqr = {1}".format(best_log_likelihood, best_distance_sqr))
    
        display(pd.DataFrame.from_dict(cluster_data).set_index('label'))
        
        print("\nCluster labels for each input data:")
        print(labels)
        print("\nCluster probabilities for each input data:")
        print(probability)
     
    #---------------------------------------------------------------------------
    # Calculate other summary data....
    #
    #  --> NOTE: Assumes labels mean an absolute assignment of clean component to that
    #      cluster.  This works fine for "KMeans", but for "GMM" the situation is
    #      more complicated and other approaches will be needed to add up the flux
    #      of each cluster, e.g.
    #
    #     # We use probabilities as follows....
    #     flux_vals = probability[data['epoch']==epoch_list[j],i]*(data[data['epoch']==epoch_list[j]])['medianFlux']
    #     flux_in_epoch = np.sum(flux_vals) 
    #
     

    # Create a pandas dataframe to cluster-epoch values.  These values are
    #  calculated directly by clean components associated with those clusters
    # 

    df_column_names = [ 'source', 'band', 
                        'ep_name','epoch','clusterID',
                        'N_Icc', 'N_QUcc',
                        'avg_x','avg_y','dist','pa',
                        'pred_x','pred_y',
                        'core_x','core_y',
                        'fwhm_maj','fwhm_min','cpa',
                        'iflux','qflux','uflux',
                        'pflux','evpa' ]
    #new_df_row = { 'source': source, 'band':band, 
    #               'ep_name' : '','epoch' : epoch_list[j],'clusterID': i,
    #               'N_Icc' : N_Icc, 'N_QUcc' : N_QUcc,
    #               'avg_x' : avg_x,'avg_y' : avg_y,'dist' : dist, 'pa' : pa,
    #               'pred_x' : pred_x,'pred_y' : pred_y,
    #               'core_x' : core_pos['x'][j],'core_y' : core_pos['y'][j],
    #               'fwhm_maj' : fwhm_maj,'fwhm_min' : fwhm_min,'cpa' : cpa,
    #               'iflux' : iflux,'qflux' : qflux,'uflux' : uflux,
    #               'pflux' : pflux,'evpa' : evpa }
    cluster_epoch_df = pd.DataFrame(columns=df_column_names)

    #
    # Compute clean component based properties for each cluster in each epoch
    #  based specifically on cc assigned to those clusters in that epoch
    # 
    # All positions are relative to the map for that epoch, do not correct
    #  for core position.  Note that the core position does need to be added
    #  into the predicted locations in each epoch based on cluster motions 
    #  which assume a zero core position in all epochs.
    #
    for i in range(-1,Nclusters):
        # create an array to simplify finding median I flux over epoch
        #  for this cluster
        iflux_over_epoch = np.zeros(len(epoch_list))
        #
        for j in range(len(epoch_list)):
            #
            # select data for just this epoch
            #
            dmask = (data['epoch'] == epoch_list[j])
            #
            # Handle unassigned data first with label = -1 or i = -1
            #
            if i == -1:
                iflux = np.sum(data[dmask*(labels == -1)*(data['stokes']=='i')]['flux'])
                if iflux < 1e-9:
                    iflux = np.nan
                qflux = np.sum(data[dmask*(labels == -1)*(data['stokes']=='q')]['flux'])
                if np.abs(qflux) < 1e-9:
                    qflux = np.nan
                uflux = np.sum(data[dmask*(labels == -1)*(data['stokes']=='u')]['flux'])
                if np.abs(uflux) < 1e-9:
                    uflux = np.nan
                N_Icc = np.sum(dmask*(labels == -1)*(data['stokes']=='i'))
                N_QUcc = np.sum(dmask*(labels == -1)*(data['stokes']!='i'))
                new_df_row = [ source, band, 
                               get_epoch_name(epoch_list[j], epoch_info), epoch_list[j], i,
                               N_Icc, N_QUcc,
                               np.nan, np.nan, np.nan, np.nan,
                               np.nan, np.nan,
                               core_pos['x'][j], core_pos['y'][j],
                               np.nan, np.nan, np.nan,
                               iflux, qflux, uflux,
                               np.nan, np.nan ]
                cluster_epoch_df.loc[len(cluster_epoch_df)] = new_df_row
                continue
            #
            # predicted positions for cluster i in epoch j
            #
            pred_x = 0.5*cluster_data['accelX'][i]*(epoch_list[j]-ref_epoch)**2+\
                     cluster_data['slopeX'][i]*(epoch_list[j]-ref_epoch)+cluster_data['centX'][i]\
                     + core_pos['x'][j]
            pred_y = 0.5*cluster_data['accelY'][i]*(epoch_list[j]-ref_epoch)**2+\
                     cluster_data['slopeY'][i]*(epoch_list[j]-ref_epoch)+cluster_data['centY'][i]\
                     + core_pos['y'][j]  
            # skip cases with no cc that has > 0.01 prob of being in that cluster
            #  --> NOTE: using "probvals" which depends on clustering method
            #            and will naturally reflect hard or soft clustering
            if not(np.any(probvals[dmask,i] > 0.01)):
                #new_df_row =   'source': source, 'band':band, 
                #               'ep_name' : '','epoch' : epoch_list[j],'clusterID': i,
                #               'N_Icc' : 0, 'N_QUcc' : 0,
                #               'avg_x' : np.nan,'avg_y' : np.nan,'dist' : np.nan, 'pa' : np.nan,
                #               'pred_x' : pred_x,'pred_y' : pred_y,
                #               'core_x' : core_pos['x'][j],'core_y' : core_pos['y'][j],
                #               'fwhm_maj' : np.nan,'fwhm_min' : np.nan,'cpa' : np.nan,
                #               'iflux' : np.nan,'qflux' : np.nan,'uflux' : np.nan,
                #               'pflux' : np.nan,'evpa' : np.nan }
                new_df_row = [ source, band, 
                               get_epoch_name(epoch_list[j], epoch_info), epoch_list[j], i,
                               0, 0,
                               np.nan, np.nan, np.nan, np.nan,
                               pred_x, pred_y,
                               core_pos['x'][j], core_pos['y'][j],
                               np.nan, np.nan, np.nan,
                               np.nan, np.nan, np.nan,
                               np.nan, np.nan ]
                cluster_epoch_df.loc[len(cluster_epoch_df)] = new_df_row
                continue
            #
            # Compute position properties
            #
            xpos = np.array((data[dmask])['x'])
            ypos = np.array((data[dmask])['y'])
            avg_x = np.sum(weights[dmask,i]*xpos)/np.sum(weights[dmask,i])
            avg_y = np.sum(weights[dmask,i]*ypos)/np.sum(weights[dmask,i])
            dist = np.sqrt(avg_x**2+avg_y**2)
            pa = np.arctan2(avg_x,avg_y)*180.0/np.pi
            #
            # Compute flux properties in this epoch
            #
            # weighted fluxes --> see "probvals" note above
            fluxvals = probvals[dmask,i]*np.array((data[dmask])['flux']) 
            fluxvals *= (probvals[dmask,i] > 0.01)   # don't include low prob. associations
            #
            imask = ((data[dmask])['stokes']=='i')
            iflux = np.sum(fluxvals[imask])
            if iflux < 1e-9:
                iflux = np.nan
                iflux_over_epoch[j] = 0.0
            else:
                iflux_over_epoch[j] = iflux
            #
            qmask = ((data[dmask])['stokes']=='q')
            if not(np.any((probvals[dmask,i])[qmask] > 0.01)):
                qflux = np.nan
            else:
                qflux = np.sum(fluxvals[qmask])
            #
            umask = ((data[dmask])['stokes']=='u')
            if not(np.any((probvals[dmask,i])[umask] > 0.01)):
                uflux = np.nan
            else:
                uflux = np.sum(fluxvals[umask])
            #
            pflux = np.sqrt(qflux**2+uflux**2)
            evpa = 0.5*np.arctan2(uflux,qflux)*180.0/np.pi
            #
            # Compute component counts
            #
            N_Icc = np.sum((probvals[dmask,i])[imask] > 0.01)
            N_QUcc = np.sum((probvals[dmask,i])[((data[dmask])['stokes']!='i')] > 0.01)
            #
            # compute spread in positions and estimate component size + orientation
            #
            nvals = np.sum(weights[dmask,i] > 0.1*np.abs(f[dmask,0]))
            if nvals >= 10:
                varx = (nvals/(nvals-1.0))*np.sum(weights[dmask,i]*(xpos-avg_x)**2)/np.sum(weights[dmask,i])
                vary = (nvals/(nvals-1.0))*np.sum(weights[dmask,i]*(ypos-avg_y)**2)/np.sum(weights[dmask,i])
                varxy = (nvals/(nvals-1.0))*np.sum(weights[dmask,i]*(xpos-avg_x)*(ypos-avg_y))/np.sum(weights[dmask,i])
                # keep it as a circle if EGauss is turned off, or if we have too few points 
                if EGauss == False or nvals < 20:
                    varx = (varx+vary)/2
                    vary = varx
                    varxy = 0.0*varxy
                # Assign the cluster size/shape in *SKY coordinates* in terms of the position angle
                # Define correlation matrix 
                corMatrix = np.array([[varx, varxy],[varxy, vary]])
                # Calculate eigenvalues and eigenvectors
                evalue, evector = np.linalg.eig(corMatrix)
                # compute Major and minor axis
                maj_index = np.argmax(evalue)
                min_index = np.argmin(evalue)
                fwhm_maj = 2.355*np.sqrt(evalue[maj_index])
                fwhm_min = 2.355*np.sqrt(evalue[min_index])
                # Use the eigenvector components of the major axis to find its position angle
                # *SKY* coordinate position angle which is relative to Y-axis in DEGREES!!
                cpa = (180/np.pi)*np.arctan2(evector[0][maj_index],evector[1][maj_index])
                # adjust pa to be > -90 and <= 90
                if cpa <= -90:  
                    cpa = cpa + 180
                elif cpa > 90:
                    cpa = cpa - 180
            else:
                fwhm_maj = 2.355*cluster_data['sizeMaj'][i]
                fwhm_min = 2.355*cluster_data['sizeMin'][i]
                cpa = cluster_data['sizePA'][i]
            #
            # Add a row to the dataframe with this information
            #
            new_df_row = [ source, band, 
                           get_epoch_name(epoch_list[j], epoch_info), epoch_list[j], i,
                           N_Icc, N_QUcc,
                           avg_x, avg_y, dist, pa,
                           pred_x, pred_y,
                           core_pos['x'][j], core_pos['y'][j],
                           fwhm_maj, fwhm_min, cpa,
                           iflux, qflux, uflux,
                           pflux, evpa ]
            cluster_epoch_df.loc[len(cluster_epoch_df)] = new_df_row
            
 
        #
        # Assign cluster flux using median stokes-I only
        #
        cluster_data[i]['medianFlux'] = np.nanmedian(iflux_over_epoch)

    # Add extra information to the cluster_epoch_df to help later
    cluster_epoch_df['select'] = False
    cluster_epoch_df['robust'] = None
    cluster_epoch_df['use_in_fit'] = None
    #
    cluster_epoch_df['ClusterType'] = ClusterType
    cluster_epoch_df['Nclusters'] = Nclusters
    cluster_epoch_df['Nepochs'] = len(epoch_list)
    cluster_epoch_df['ep_min'] = epoch_list[0]
    cluster_epoch_df['ep_max'] = epoch_list[-1]
    cluster_epoch_df['ref_epoch'] = ref_epoch
    cluster_epoch_df['origID'] = cluster_epoch_df['clusterID']
    cluster_epoch_df['medianFlux'] = np.nan    
    cluster_epoch_df['centX'] = np.nan
    cluster_epoch_df['centY'] = np.nan
    cluster_epoch_df['dcentX'] = np.nan
    cluster_epoch_df['dcentY'] = np.nan
    cluster_epoch_df['slopeX'] = np.nan
    cluster_epoch_df['slopeY'] = np.nan
    cluster_epoch_df['dslopeX'] = np.nan
    cluster_epoch_df['dslopeY'] = np.nan
    cluster_epoch_df['accelX'] = np.nan
    cluster_epoch_df['accelY'] = np.nan
    cluster_epoch_df['daccelX'] = np.nan
    cluster_epoch_df['daccelY'] = np.nan
    cluster_epoch_df['sizeMaj'] = np.nan
    cluster_epoch_df['sizeMin'] = np.nan
    cluster_epoch_df['sizePA'] = np.nan

    for i in range(Nclusters):
        label_mask=(cluster_epoch_df['clusterID']==i)
        cluster_epoch_df.loc[label_mask,'medianFlux'] = cluster_data[i]['medianFlux']
        cluster_epoch_df.loc[label_mask,'centX'] = cluster_data[i]['centX']
        cluster_epoch_df.loc[label_mask,'centY'] = cluster_data[i]['centY']
        cluster_epoch_df.loc[label_mask,'dcentX'] = cluster_data[i]['dcentX']
        cluster_epoch_df.loc[label_mask,'dcentY'] = cluster_data[i]['dcentY']
        cluster_epoch_df.loc[label_mask,'slopeX'] = cluster_data[i]['slopeX']
        cluster_epoch_df.loc[label_mask,'slopeY'] = cluster_data[i]['slopeY']
        cluster_epoch_df.loc[label_mask,'dslopeX'] = cluster_data[i]['dslopeX']
        cluster_epoch_df.loc[label_mask,'dslopeY'] = cluster_data[i]['dslopeY']
        cluster_epoch_df.loc[label_mask,'accelX'] = cluster_data[i]['accelX']
        cluster_epoch_df.loc[label_mask,'accelY'] = cluster_data[i]['accelY']
        cluster_epoch_df.loc[label_mask,'daccelX'] = cluster_data[i]['daccelX']
        cluster_epoch_df.loc[label_mask,'daccelY'] = cluster_data[i]['daccelY']
        cluster_epoch_df.loc[label_mask,'sizeMaj'] = cluster_data[i]['sizeMaj']
        cluster_epoch_df.loc[label_mask,'sizeMin'] = cluster_data[i]['sizeMin']
        cluster_epoch_df.loc[label_mask,'sizePA'] = cluster_data[i]['sizePA']
        
    # 
    # Calculate "overlap" in the form of 1-max(probability) for each point
    #.  associated with each cluster, flux-weighted over epoch, so we have
    #.  the flux-weighted degree that each cluster overlaps with other clusters
    #.  as a function of epoch
    #
    overlaps = np.zeros((Nclusters,len(epoch_list)))
    for i in range(1, Nclusters):
        max_overlap = 1.0-1.0/(i+1)  # maximum overlap a component can have with other clusters.
        for j in range(0,len(epoch_list)):
            dmask = (labels==i)&(data['epoch']==epoch_list[j])
            if not(np.any(dmask)):
                overlaps[i,j] = np.nan
                continue
            fluxvals = np.abs(np.array((data[dmask])['flux']))      # assure positive in case 'q', 'u' cases
            if np.sum(fluxvals) > 0:
                weighted_olap_sq = np.sum(fluxvals*(1.0-np.max(probability[dmask],axis=1))**2)/np.sum(fluxvals)
                overlaps[i,j] = np.sqrt(weighted_olap_sq)/max_overlap
                #overlaps[i,j] = (1.0-np.median(np.max(probability[dmask],axis=1)))/max_overlap
            else:
                overlaps[i,j] = np.nan
    
    # Also compute a net overlap and a net fraction used of the data
    #  --> make fluxes positive to accomodate possible q, u fluxes
    net_frac_cc_used = np.sum(labels > -1)/len(labels)
    net_frac_flux_used = np.sum(np.abs(data['flux'][labels > -1]))/np.sum(np.abs(data['flux']))
    
    mask = labels > -1
    if Nclusters > 1:
        net_overlap = np.nanmean(overlaps)
        #net_overlap = 1.0-np.sum(data['flux'][mask]*np.max(probability[mask],axis=1))\
        #             /np.sum(data['flux'][mask])
    else:
        net_overlap = np.nan
        
        
    #---------------------------------------------------------------------------
    # Create results dictionary, make plots, and return results
    #
       
    result_dict = { 
             'ref_epoch':ref_epoch,                # reference epoch
             'Nclusters':Nclusters,                # number of clusters fit
             'cluster_data':cluster_data,          # cluster data
             'cluster_epoch_df':cluster_epoch_df,  # data frame with cluster properties calculated
                                                   #   in each epoch using clean component assigned.
                                                   #   Positions are relative to the map, i.e. the core
                                                   #   position has not been removed from the data
             'labels':labels,                      # labels for input data
             #'probability':probability,            # probability for input data to each cluster
             'dist_sqr':best_distance_sqr,             # mean square distance metric (smaller = better)
             #'distXsqr':save_mean_distXsqr,
             #'distYsqr':save_mean_distYsqr,
             'log_likelihood':best_log_likelihood, # log likelihood metric (large = better)
             'core_pos':core_pos,                  # solved core positions (x,y) per epoch
             #'overlaps':overlaps,                  # Overlap (flux-weighted) of each point from its
             #                                      #.  cluster over epoch (cluster, epoch)
             'net_overlap':net_overlap,            # Net overlap between clusters for whole dataset
             'frac_cc_used':net_frac_cc_used,      # Fraction of clean components used
             'frac_flux_used':net_frac_flux_used    # Same as above, weighted by flux
            }

    #
    # return results
    #
    return result_dict
          
#------------------------------------------------------ 
# test_cluster_num
#------------------------------------------------------ 
# This function runs the clustering many times over a
#  range of cluster numbers and returns the results
#
def test_cluster_num(source, band, ccdata, ep_info,
                     min_clusters, max_clusters, 
                     min_ep=0, max_ep=3000, groups=[0],   # min,max epochs and data groups to use
                     Fit_Accel = True,
                     ClusterType = "KMeans", EGauss = True, 
                     print_info=False, 
                     print_diag=False, CoreIDMethod="JetEnd",
                     JetDir=None,
                     RefEpochType="Middle", # "Middle" or "Median"
                     SigmaCut = 0.0, StationaryCore = True,
                     StokesQU_weight = 1e-9,
                     sfile=None,
                     input_core_pos=None,
                     start_guess=None):
                     
    if start_guess is None:
        start_guess = np.array([], dtype = cluster_datatype)    
    #
    # Get specific data to test: min epoch, max epoch, and data labels only
    #
    test_data, test_epochs, core_pos = select_epoch_range(ccdata,ep_info,min_ep,max_ep,groups,input_core_pos,show_info=True)

    #
    # Run set of cases and report results
    #
    results = np.array([])
    clusters = np.array([])
    # Create a sorted list of epochs in data
    epoch_list = test_epochs['epoch_val']
    epochs = len(epoch_list)
    #
    # mean beam and flux stats
    #
    mean_total_iflux = np.sum(np.abs(test_data[test_data["stokes"]=='i']['flux']))/len(epoch_list)
    mean_inoise_cut = np.mean(test_epochs['sigma_cut']*test_epochs['inoise'])
    Ndata_mean_inoise_cut = mean_total_iflux/(mean_inoise_cut)
    mean_sum_beam_sqr = np.mean( (test_epochs['bmaj']**2 + test_epochs['bmin']**2) )    
    #
    # Get a count of data, adjust for any stokes weighting
    #
    Ndata = len(test_data[test_data['stokes']=='i'])+StokesQU_weight*len(test_data[test_data['stokes']!='i'])
    Ndata_mean = Ndata/epochs
    #
    # be sure that max_clusters does not exceed the number of data points we have, 
    #  otherwise we will get errors from the clustering code
    #
    max_clusters = np.min([max_clusters, Ndata]).astype(int)
    # Iterate over possible cluster numbers and calculate models
    #
    for nclusters in range(min_clusters, max_clusters+1):
        # get information on core locations from previous run...
        #  --> Also reset starting cluster property guess if we are doing 
        #      a different number of clusters than minimum
        if nclusters > min_clusters:
            input_core_pos = np.copy(test_run['core_pos'])
            start_guess = np.array([], dtype = cluster_datatype)
        #
        if ClusterType == 'GMM':
            test_run1 = Find_Moving_Clusters(source, band, test_data, nclusters, ClusterType="KMeans", 
                                           EGauss=EGauss, print_info=print_info, 
                                           input_core_pos = core_pos, print_diag=print_diag,
                                           CoreIDMethod=CoreIDMethod, epoch_info=test_epochs,
                                           JetDir=JetDir,
                                           Fit_Accel=Fit_Accel,
                                           RefEpochType=RefEpochType,
                                           StokesQU_weight=StokesQU_weight,
                                           SigmaCut=SigmaCut, StationaryCore=StationaryCore,start_guess=start_guess)
            test_run = Find_Moving_Clusters(source, band, test_data, nclusters, ClusterType="GMM", 
                                           EGauss=EGauss, print_info=print_info, 
                                           input_core_pos = test_run1['core_pos'], 
                                           input_core_id = 0, # Always will be zero when using starting_guess from last run
                                           print_diag=print_diag, epoch_info=test_epochs,
                                           RefEpochType=RefEpochType,
                                           Fit_Accel=Fit_Accel,
                                           StokesQU_weight=StokesQU_weight,
                                           SigmaCut=SigmaCut, StationaryCore=StationaryCore,
                                           start_guess=test_run1['cluster_data'])
        else:    
            test_run = Find_Moving_Clusters(source, band, test_data, nclusters, ClusterType=ClusterType, 
                                           EGauss=EGauss, print_info=print_info, 
                                           input_core_pos = core_pos, print_diag=print_diag,
                                           CoreIDMethod=CoreIDMethod, epoch_info=test_epochs,
                                           JetDir=JetDir,
                                           RefEpochType=RefEpochType,
                                           Fit_Accel=Fit_Accel,
                                           StokesQU_weight=StokesQU_weight,
                                           SigmaCut=SigmaCut, StationaryCore=StationaryCore,start_guess=start_guess)
        # extract key results    
        frac_cc = test_run['frac_cc_used']
        frac_fl = test_run['frac_flux_used']
        overlap = test_run['net_overlap']
        #
        k = (nclusters-int(StationaryCore))*4
        if Fit_Accel and epochs > 9:
            k = (nclusters-int(StationaryCore))*6   # degrees of freedom from cluster positions and slopes
        if StationaryCore:
            k += epochs*2  # for floating core positions
        #
        print("%d <d_sq>= %6.4f log_like= %6.3e f_cc= %.3f f_fl=%.3f olap= %.3f" % 
               (nclusters, test_run['dist_sqr'], test_run['log_likelihood'],frac_cc,frac_fl,overlap))
        # created pd dataframe from the cluster results
        test_df = pd.DataFrame.from_dict(test_run['cluster_data']).set_index('label')
        test_df['Nclusters'] = nclusters
        test_df['Ndata'] = Ndata
        test_df['NIdata'] = len(test_data[test_data['stokes']=='i'])
        test_df['NQUdata'] = len(test_data[test_data['stokes']!='i'])
        test_df['Ndata_mean'] = Ndata_mean
        test_df['Ndata_mean_inoise_cut'] = Ndata_mean_inoise_cut
        test_df['k'] = k
        test_df['mean_sum_beam_sqr'] = mean_sum_beam_sqr
        test_df['mean_dsqr'] = test_run['dist_sqr']
        test_df['log_like'] = test_run['log_likelihood']
        test_df['frac_cc'] = frac_cc
        test_df['frac_fl'] = frac_fl
        test_df['overlap'] = overlap
        test_df['Code_Vers'] = CCVERS
        test_df['ClusterType'] = ClusterType
        test_df['RefEpochType'] = RefEpochType
        test_df['StokesQU_weight'] = StokesQU_weight
        test_df['SigmaCut'] = SigmaCut
        test_df['StationaryCore'] = StationaryCore
        test_df['ID'] = list(test_df.index.values)
        
        if nclusters == min_clusters:
            cluster_df = test_df.copy()
        else:
            cluster_df = pd.concat([cluster_df,test_df], ignore_index=True)
        
        results = np.append(results, test_run)
        clusters = np.append(clusters, nclusters)


    cluster_df = cluster_df.reindex(columns=['Nclusters','Ndata','NQUdata','Ndata_mean','Ndata_mean_inoise_cut',
                                             'Code_Vers',
                                             'ClusterType', 'StokesQU_weight',
                                             'SigmaCut','StationaryCore',
                                             'mean_dsqr','log_like',
                                             'mean_sum_beam_sqr', 'k',
                                             'frac_cc','frac_fl','overlap',
                                             'ID','epoch','centX','dcentX','centY','dcentY',
                                             'slopeX','dslopeX','slopeY','dslopeY',
                                             'accelX','daccelX','accelY','daccelY',
                                             'medianFlux','sizeMaj','sizeMin','sizePA'])

    if sfile is not None:
        np.savez_compressed(sfile,vers=CCVERS,data=test_data,ep_info=test_epochs,
                       test_results=results,clusters=clusters,
                       allow_pickle=True)
        cluster_df.to_csv(sfile+".csv",index=False)
    
    return test_data, test_epochs, results, clusters, cluster_df


#
# Function to extract single N results from an array of test results
#
def get_fit_results(result_dict):
    #print(result_dict)
    
    Nclusters = result_dict['Nclusters']
    ref_epoch = result_dict['ref_epoch']
    cluster_data = result_dict['cluster_data']
    cluster_epoch_df = result_dict['cluster_epoch_df']
    labels = result_dict['labels']
    #probability = result_dict['probability']
    #overlaps = result_dict['overlaps']
    core_pos = result_dict['core_pos']

    return Nclusters, ref_epoch, cluster_data,\
           cluster_epoch_df,\
           labels, core_pos
           #labels, probability, overlaps, core_pos\

#------------------------------------------------------ 
# show_clusters
#------------------------------------------------------ 
# This function replicates the info display and 
#  plotting done in the Find_Moving_Cluster
#  function, but it could be improved to do more.
#
def show_clusters(ccdata, epoch_info, root_data_dir,
                  labels=None,cluster_epoch_df=None,
                  result_dict=None, cluster_array=None, N=None,
                  flux_threshold=0.0, z = 0, print_Tb=False,
                  show_overlays=True, colorImages=False,
                  xshift=None, yshift=None, lims=None, run_history=None):
    
    # Create a list of epochs in data
    epoch_list = epoch_info['epoch_val']

    #
    # Check if we've been given the results for one set
    #  of clusters and labels, or if we have a full set of
    #  options to try (varying Ncluster value
    #
    change_N = False # don't allow change to number of clusters unless
                     #   we have sufficient information
    if cluster_epoch_df is None or labels is None:
        if result_dict is not None and\
           cluster_array is not None and\
           N is not None:
            #  
            # Extract results from results dictionary
            #    
            Nclusters,ref_epoch,cluster_data,cluster_epoch_df,\
            labels,core_pos =\
                get_fit_results(result_dict[cluster_array==N][0])
            #
            change_N = True   # allow change to number of clusters
            #
        else:
            print("Missing some info needed to make plots")
            return None, None

    # get max, min positions relative to the core for defining plotting area
    #  --> include cluster size estimates 
    xpos = cluster_epoch_df['avg_x']-cluster_epoch_df['core_x']
    ypos = cluster_epoch_df['avg_y']-cluster_epoch_df['core_y']
    #xpos = cluster_epoch_df['centX']
    #ypos = cluster_epoch_df['centY']
    median_beam = np.nanmedian(epoch_info['bmaj'])
    xmin = np.min(xpos - 2*cluster_epoch_df['sizeMaj']) - 1.5*median_beam
    xmax = np.max(xpos + 2*cluster_epoch_df['sizeMaj']) + 1.5*median_beam
    ymin = np.min(ypos - 2*cluster_epoch_df['sizeMaj']) - 1.5*median_beam
    ymax = np.max(ypos + 2*cluster_epoch_df['sizeMaj']) + 1.5*median_beam
    xspan = xmax-xmin
    yspan = ymax-ymin
    xrange = [ xmin - 0.05*xspan , xmax + 0.05*xspan ]
    yrange = [ ymin - 0.05*yspan , ymax + 0.05*yspan ]
    if lims is None:
        lims=[xrange[1],xrange[0],yrange[0],yrange[1]]

    # compute shifts for images and clean components
    if xspan < yspan:
        xshift = 0.7*xspan
        yshift = 0
    else:
        yshift = -0.7*yspan
        xshift = 0          

    # Setup summary plots
    s_fig, s_axs = plt.subplots(2,2,layout="constrained")
    s_fig.suptitle(" ", fontsize=8)
    if np.any(cluster_epoch_df['pflux'] > 0.0):
        plot_pol = True
    else:
        plot_pol = False
    
    # make a column for whether a component is selected for an operation
    cluster_epoch_df['select'] = False

    #
    # Create summary plots
    #
    alternate_plots = "Tb"
    make_summary_plots(s_fig, s_axs, cluster_epoch_df, 
                       flux_threshold=flux_threshold, plot_pol=plot_pol,
                       alternate_plots=alternate_plots, z=z, print_Tb=print_Tb,
                       xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
    s_fig.show()
    s_fig.canvas.draw_idle()

    #
    # show help for basic operations
    #
    print("--------------------------------------------------------------")
    print("-----------------------")
    print("Overplot window keys:")
    print("-----------------------")
    print("n OR right arrow = show next epoch")
    print("b OR left arrow = show previous epoch")
    print("-----------------------")
    print("Multiplot window keys:")
    print("-----------------------")
    print("shift + click = select cluster under cursor")
    print("              -> works in position or flux vs. time figures")
    print("              -> selections are toggled on/off by repeating")
    print("i = change the ID of all selected clusters")
    print("    -> will ask for a new cluster ID in the console window")
    print("a = change ID of all clusters with the selectedID to a new value")
    print("    -> will ask for a new cluster ID in the console window")
    print("    -> only a single point can be selected at a time for this operation")
    print("u = toggle the use_in_fit flag for the selected points")
    print("r = toggle the robust flag for the selected clusterIDs")
    print("    -> will change robustness for these clusterIDs in all epochs")
    print("b = show Tb")
    print("v = show vector speeds")
    print("p = show polarization, if available")
    print("--------------------------------------------------------------")

    #
    # Allow and manage some keypress events in window
    # 
    def plot_on_press(event):
        # update cluster number if needed...
        cl_ep_df = cluster_epoch_df
        cl_labels = labels
        if show_overlays:
            if change_N and int(sclusters.val) != N:
                temp1, temp2, temp3,\
                cl_ep_df,cl_labels, temp4 =\
                    get_fit_results(result_dict[cluster_array==int(sclusters.val)][0])
        if event.key in ['u','i','r','a']:  # toggle use_in_fit, change ID of selected clusters, or toggle robust flag
            update_clusterIDs(cl_ep_df, event.key, run_history=run_history)
            make_summary_plots(s_fig, s_axs, cl_ep_df, 
                               flux_threshold=flux_threshold, plot_pol=plot_pol,
                               alternate_plots="Tb", z=z, print_Tb=print_Tb,
                               xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
            s_fig.canvas.draw_idle()
        if event.key == 'b':                 # plot brightness and position over time
            make_summary_plots(s_fig, s_axs, cl_ep_df, 
                               flux_threshold=flux_threshold, plot_pol=plot_pol,
                               alternate_plots="Tb", z=z, print_Tb=print_Tb,
                               xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
            s_fig.canvas.draw_idle()
        if event.key == 'v':                 # plot speeds and vector velocities
            make_summary_plots(s_fig, s_axs, cl_ep_df, 
                               flux_threshold=flux_threshold, plot_pol=plot_pol,
                               alternate_plots="Speed", z=z, print_Tb=print_Tb,
                               xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
            s_fig.canvas.draw_idle()
        if event.key == 'p':                 # plot polarization
            make_summary_plots(s_fig, s_axs, cl_ep_df, 
                               flux_threshold=flux_threshold, plot_pol=plot_pol,
                               alternate_plots="Pol", z=z, print_Tb=print_Tb,
                               xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
            s_fig.canvas.draw_idle()

    s_fig.canvas.mpl_connect('key_press_event', plot_on_press)

    #
    # Add a callback for clicking on image to select a datapoint
    #
    def plot_on_click(event):
        # update cluster number if needed...
        cl_ep_df = cluster_epoch_df
        cl_labels = labels
        if show_overlays:
            if change_N and int(sclusters.val) != N:
                temp1, temp2, temp3,\
                cl_ep_df,cl_labels, temp4 =\
                    get_fit_results(result_dict[cluster_array==int(sclusters.val)][0])
        #
        # select a datapoint
        # 
        if event.key == 'shift' and event.xdata is not None:
            times = np.unique(cl_ep_df['epoch'])
            if np.min(np.abs(times - event.xdata)) > 1.0:
                return
            time_i = np.argmin(np.abs(times-event.xdata))
            # create a mask that filters on selected time and requires real clusterIDs
            timeID_mask = (cl_ep_df['epoch'] == times[time_i])*(cl_ep_df['clusterID'] >= 0)
            #print(time_i, times[time_i])
            xpos = np.array(cl_ep_df['avg_x']) - np.array(cl_ep_df['core_x'])
            ypos = np.array(cl_ep_df['avg_y']) - np.array(cl_ep_df['core_y'])
            dist = np.sqrt(xpos**2+ypos**2)
            # get a distance datapoint
            if event.inaxes == s_axs[0,0]:
                yvals = dist[timeID_mask]
                yval_i = np.nanargmin(np.abs(yvals-event.ydata))
                #print(yval_i,yvals[yval_i],yvals)
                datapoint = np.argwhere((timeID_mask)&(dist==yvals[yval_i]))[0][0]
                #print(datapoint)
            # get a flux datapoint    
            elif event.inaxes == s_axs[0,1]:
                yvals = np.array(np.log10(cl_ep_df[timeID_mask]['iflux']))
                yval_i = np.nanargmin(np.abs(yvals-event.ydata))
                #print(yval_i,yvals[yval_i])
                datapoint = np.argwhere((timeID_mask)&(np.log10(cl_ep_df['iflux'])==yvals[yval_i]))[0][0]
                #print(datapoint)
            else:
                print("Shift-Click on distance or flux plot to select a datapoint")
                return
            # Toggle this point as selected, but only allow one selected point per timestamp
            if cl_ep_df.loc[cl_ep_df.index[datapoint],'select']:
                cl_ep_df.loc[cl_ep_df.index[datapoint],'select'] = False
            else:
                # first clear any other selected points at this epoch
                cl_ep_df.loc[(cl_ep_df['epoch']==times[time_i]),'select'] = False
                # then select this one  
                cl_ep_df.loc[cl_ep_df.index[datapoint],'select'] = True  
                print("Selected clusterID {0} at epoch {1} with flux {2:.2f} mJy".format(
                    cl_ep_df.loc[cl_ep_df.index[datapoint],'clusterID'], cl_ep_df.loc[cl_ep_df.index[datapoint],'epoch'],
                    1000.0*cl_ep_df.loc[cl_ep_df.index[datapoint],'iflux']))  
            # updated make_summary_plots window
            make_summary_plots(s_fig, s_axs, cl_ep_df, 
                               flux_threshold=flux_threshold, plot_pol=plot_pol,
                               alternate_plots="Tb", z=z, print_Tb=print_Tb,
                               xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
            s_fig.canvas.draw_idle()

    s_fig.canvas.mpl_connect('button_press_event', plot_on_click)    


    #
    # exit if we are not showing overlays
    #
    if not(show_overlays):
        return s_fig, None

    else:
        #
        # show an image with cluster superimposed
        #
        epoch_int=0  # integer value for epoch, from 0 to epochs-1
        if colorImages:
            cmap = cm.cubehelix_r
            ptype = 'Color'
        else:
            cmap = None
            ptype = 'Contour'
        fig, ax, cb = overplot_clusters(epoch_info, ccdata, root_data_dir,
                                 cluster_epoch_df, labels, epoch_int,
                                  xshift = xshift, yshift = yshift,
                                 lims=lims, cmap=cmap, ptype=ptype)
        fig.show()
        fig.canvas.draw_idle()  

        #
        # Create room for epoch and possibly cluster slider
        #
        if change_N:
            fig.subplots_adjust(bottom=0.25)
        else:
            fig.subplots_adjust(bottom=0.15)
            
        #
        # Add an epoch slider
        #
        ax_epochs = fig.add_axes([0.15, 0.01, 0.65, 0.03])
        
        sepochs = Slider(
            ax_epochs, "Epoch", epoch_list[0], epoch_list[-1],
            valinit=epoch_list[0], valstep=np.array(epoch_list),
            color="green"
        )
        
        def update_epoch(val):
            # update cluster number if needed...
            if change_N and int(sclusters.val) != N:
                temp1, temp2, temp3,\
                cl_ep_df,cl_labels, temp4 =\
                    get_fit_results(result_dict[cluster_array==int(sclusters.val)][0])
            else:
                cl_ep_df = cluster_epoch_df
                cl_labels = labels
            epoch_int=np.argwhere(epoch_list==sepochs.val)[0][0]
            #ax.set_title("{0} {1}".format(sepoch.val, j))
            overplot_clusters(epoch_info, ccdata, root_data_dir,
                                 cl_ep_df, cl_labels, epoch_int,
                                  xshift = xshift, yshift = yshift,
                                 lims=lims,fig=fig,ax=ax,cb=cb, cmap=cmap, ptype=ptype)
            #l.set_ydata(amp*np.sin(2*np.pi*freq*t))
            s_fig.canvas.draw_idle()
            fig.canvas.draw_idle()
            
        sepochs.on_changed(update_epoch)

        #
        # If we allow change to number of clusters, enable that...
        #
        if change_N:
            ax_clusters = fig.add_axes([0.15, 0.05, 0.65, 0.03])
            
            sclusters = Slider(
                ax_clusters, "Clusters", cluster_array[0], cluster_array[-1],
                valinit=N, valstep=cluster_array,
                color="blue"
            )

            def update_clusters(val):
                epoch_int=np.argwhere(epoch_list==sepochs.val)[0][0]
                # get new values for new cluster fit
                Nclusters, ref_epoch, cluster_data,\
                cluster_epoch_df,labels, core_pos=\
                    get_fit_results(result_dict[cluster_array==int(sclusters.val)][0])
                #
                make_summary_plots(s_fig, s_axs, cluster_epoch_df, 
                                   flux_threshold=flux_threshold, plot_pol=plot_pol,
                                   alternate_plots=alternate_plots, z=z, print_Tb=print_Tb,
                                   xlims=[lims[0],lims[1]], ylims=[lims[2],lims[3]])
                # remake overlay plot too
                overplot_clusters(epoch_info, ccdata, root_data_dir,
                                     cluster_epoch_df, labels, epoch_int,
                                      xshift = xshift, yshift = yshift,
                                     lims=lims,fig=fig,ax=ax,cb=cb, cmap=cmap, ptype=ptype)
                #l.set_ydata(amp*np.sin(2*np.pi*freq*t))
                s_fig.canvas.draw_idle()
                fig.canvas.draw_idle()

                return cluster_epoch_df, labels

            sclusters.on_changed(update_clusters)
        else:
            sclusters = None
            

        #
        # Allow and manage some keypress events
        # 
        def on_press(event):
            # update cluster number if needed...
            if change_N and int(sclusters.val) != N:
                temp1, temp2, temp3,\
                cl_ep_df,cl_labels, temp4 =\
                    get_fit_results(result_dict[cluster_array==int(sclusters.val)][0])
            else:
                cl_ep_df = cluster_epoch_df
                cl_labels = labels
            if (event.key == 'n' or event.key == 'right') and sepochs.val < sepochs.valmax:
                current_ep = np.argwhere(epoch_list==sepochs.val)[0][0]
                sepochs.valinit = epoch_list[current_ep+1]
                sepochs.reset()
                overplot_clusters(epoch_info, ccdata, root_data_dir,
                            cl_ep_df, cl_labels, current_ep+1,
                             xshift = xshift, yshift = yshift,
                            lims=lims,fig=fig,ax=ax,cb=cb, cmap=cmap, ptype=ptype)      
                fig.canvas.draw_idle()
            if (event.key == 'b' or event.key == 'left') and sepochs.val > sepochs.valmin:
                current_ep = np.argwhere(epoch_list==sepochs.val)[0][0]
                sepochs.valinit = epoch_list[current_ep-1]
                sepochs.reset()
                overplot_clusters(epoch_info, ccdata, root_data_dir,
                            cl_ep_df, cl_labels, current_ep-1,
                             xshift = xshift, yshift = yshift,
                            lims=lims,fig=fig,ax=ax,cb=cb, cmap=cmap, ptype=ptype)      
                fig.canvas.draw_idle()
    
        fig.canvas.mpl_connect('key_press_event', on_press)

        
    return s_fig, fig  # return fig references so we don't lose access

#
# function to make the summary plots for merged clusters
#
def make_summary_plots(fig, axs, cluster_epoch_df, 
                        flux_threshold=0.0,
                        xlims=None, ylims=None, plot_comps="All", 
                        z=0, print_Tb=False,
                        plot_pol=False, alternate_plots="Tb"):

    if xlims is None:
        xlims = []
    if ylims is None:
        ylims = []

    # if no figure was provided, assume we want a six panel summary instead 
    if fig is None or axs is None:
        plt.rcParams['figure.figsize'] = [8, 10]
        fig, axs = plt.subplots(3,2,layout="constrained")
        fig.suptitle(" ", fontsize=8)
        alternate_plots = "Tb"
        six_panel = True
    else:
        six_panel = False   

    # remove previous plotting stuff
    axs[0,0].cla()
    axs[1,1].cla()
    axs[0,1].cla()
    axs[1,0].cla()

    # setup variables for basic cluster motion fits
    speeds = np.array([])
    speedX = np.array([])
    speedY = np.array([])
    median_dist = np.array([])
    median_X = np.array([])
    median_Y = np.array([])
    id_val = np.array([],dtype=int)

    #
    # Do a quick check to see if we need a pa adjustment for this source
    #
    non_core_mask = (cluster_epoch_df['clusterID']>0)
    x_c = np.array(cluster_epoch_df['avg_x'][non_core_mask]\
                   -cluster_epoch_df['core_x'][non_core_mask])
    y_c = np.array(cluster_epoch_df['avg_y'][non_core_mask]\
                   -cluster_epoch_df['core_y'][non_core_mask])
    pa_c = (180.0/np.pi)*np.arctan2(x_c,y_c)
    if np.nanmedian(np.abs(pa_c)) > 120:
        shift_pa = True
    else:
        shift_pa = False
    
    # count robust clusters for labelling
    robust_count = 0

    #
    # plot the mean positions of input data by cluster and epoch 
    #   -colors indicated associated moving clusters
    #   -include lines showing evolution of moving clusters
    #
    for i in np.unique(cluster_epoch_df['clusterID']):
        if not(plot_comps=='All' or plot_comps is None) and i not in plot_comps:
            continue
        label_mask = (cluster_epoch_df['clusterID']==i)
        # also filter on the number of cc
        #label_mask *= (cluster_epoch_df['N_Icc'] > 9)
        avgx = np.array(cluster_epoch_df['avg_x'][label_mask]).copy()
        avgy = np.array(cluster_epoch_df['avg_y'][label_mask]).copy()
        time = np.array(cluster_epoch_df['epoch'][label_mask]).copy()
        N_Icc = np.array(cluster_epoch_df['N_Icc'][label_mask]).copy()
        use_in_fit = np.array(cluster_epoch_df['use_in_fit'][label_mask]).copy()
        robust = cluster_epoch_df['robust'][label_mask].iloc[0]
        #
        if robust:
            robust_count += 1
        #
        # Correct positions for fitted core location in each epoch
        #
        xpos = avgx - np.array(cluster_epoch_df['core_x'][label_mask])
        ypos = avgy - np.array(cluster_epoch_df['core_y'][label_mask])
        dist = np.sqrt(xpos**2+ypos**2)
        pa = (180.0/np.pi)*np.arctan2(xpos,ypos)
        # require some consistency in pa trend over time
        for j in range(1,len(pa)):
            if pa[j] - pa[j-1] > 300:
                pa[j] -= 360
            elif pa[j] - pa[j-1] < -300:
                pa[j] += 360
        #
        if shift_pa:
            pa = pa + (pa < -60.0)*360.0
        #
        size = np.array(np.sqrt(cluster_epoch_df['fwhm_maj'][label_mask]*cluster_epoch_df['fwhm_min'][label_mask]))
        size[size < 0.1] = 0.1  # set a minimum size of 0.1 mas
        #
        divisor = np.sqrt(N_Icc)
        divisor[divisor == 0.0] = 1 # avoid divide by zero
        dist_std = size/divisor
        #
        flux = np.array(cluster_epoch_df['iflux'][label_mask]).copy()
        #
        # check flux threshold
        #
        if not(np.any(flux > 0)) or np.nanmedian(flux) < flux_threshold:
            continue
        #
        pflux = np.array(cluster_epoch_df['pflux'][label_mask]).copy()
        evpa = np.array(cluster_epoch_df['evpa'][label_mask]).copy()
        # require some consistency in evpa trend over time
        for j in range(1,len(evpa)):
            if evpa[j] - evpa[j-1] > 150:
                evpa[j] -= 180
            elif evpa[j] - evpa[j-1] < -150:
                evpa[j] += 180
        #
        freq = 15.4
        tb_obs = 1.22e12*flux*(1.0+z)/(freq**2*size**2)  # assumes 15.4 GHz and minimum size of 0.1 mas and redshift z = 0.0
        #
        if print_Tb and i == 0:
            print("Sizes:\n",size)
            for j in range(len(tb_obs)):
               print("Epoch: {0:.4f}, Tb_obs: {1:.3e} K".format(time[j],tb_obs[j]))
            print("Median Tb_obs pre- 2019: {0:.3e} K".format(np.median(tb_obs[time < 2019])))
            print("Median Tb_obs post-2019: {0:.3e} K".format(np.median(tb_obs[time > 2019])))
        # check flux threshold before continuing
        #if np.nanmedian(flux) < flux_threshold:
        #    continue

        # get selection mask for which epochs of this cluster are selected
        select_mask = np.array(cluster_epoch_df['select'][label_mask]).copy()

        # 
        # Setup basic plot styles based on comp. number
        #
        line_style = ":"
        cval = cl_colors[i%(len(cl_colors))]
        marker_style = cl_markers[i%(len(cl_markers))]
        fill_style = cl_fill[i%(len(cl_markers))]
        zorder = 2
        if i >= 1000 or i < 0:
            marker_style = "+"
            fill_style="none"
            cval = 'k'
        if not(robust):
            cval = 'c'
            zorder = 1
        
        #
        # Fit xpos, ypos slopes
        #
        valid_mask = (~np.isnan(xpos))*(~np.isnan(ypos))*use_in_fit
        if len(time[valid_mask]) > 4 and i < 1000 and i >= 0 and robust:        
            px, corrx = np.polyfit(time[valid_mask],xpos[valid_mask],deg=1,cov=True)
            py, corry = np.polyfit(time[valid_mask],ypos[valid_mask],deg=1,cov=True)
            # predicted distance from the origin
            pred_x = px[0]*time+px[1]
            pred_y = py[0]*time+py[1] 
            pred_dist = np.sqrt(pred_x**2+pred_y**2)
            #
            # see if this is a reliable fit OR a slow speed, so we can plot on the graph
            #
            if (px[0]**2+py[0]**2)/np.sqrt(px[0]**2*corrx[0][0]+py[0]**2*corry[0][0]) >= 3.0\
                or (np.sqrt(px[0]**2+py[0]**2) < 0.05 and np.sqrt(corrx[0,0]+corry[0,0]) < 0.05):
                #
                # set variables for later plots
                speedX = np.append(speedX, px[0])
                speedY = np.append(speedY, py[0])
                speeds = np.append(speeds, np.sqrt(px[0]**2+py[0]**2))
                median_dist = np.append(median_dist, np.median(pred_dist))
                median_X = np.append(median_X, np.median(pred_x))
                median_Y = np.append(median_Y, np.median(pred_y))
                id_val = np.append(id_val, i)
        #
        else: 
            pred_dist = np.array([0])
            

        #
        # We cannot plot positions for unassigned clean components, so skip these
        #
        if i >= 0:
            #
            # Plot the distance vs. time data for this cluster... 
            #. --> don't include a label for non-robust clusters
            #. --> mark epochs with use_in_fit = 0 with a / over top
            if robust:
                axs[0,0].plot(time,dist,marker_style,color=cval,label="{0}".format(i),
                            fillstyle=fill_style,zorder=zorder)
            else:
                axs[0,0].plot(time,dist,marker_style,color=cval,
                            fillstyle=fill_style,zorder=zorder)
 
            axs[0,0].plot(time[~use_in_fit],dist[~use_in_fit],marker='$/$',color='k',
                        fillstyle=fill_style,zorder=zorder+0.1,ls='')

            if len(pred_dist) > 4 and i > 0:
                axs[0,0].plot(time,pred_dist,color=cval,ls=line_style)

            # overlay selected comps
            if np.any(select_mask):
                axs[0,0].plot(time[select_mask],dist[select_mask],'D',color="yellow",
                              fillstyle="none")            


        #
        # Plot fluxes for this cluster, here we can include unassigned components
        # 
        axs[0,1].plot(time,np.log10(flux),color=cval,
                    marker=marker_style, fillstyle=fill_style, 
                    ls=line_style, zorder=zorder)
        axs[0,1].plot(time[~use_in_fit],np.log10(flux[~use_in_fit]),marker='$/$',color='k',
                        fillstyle=fill_style,zorder=zorder+0.1,ls='')
        # overlay selected comps
        if np.any(select_mask):
            axs[0,1].plot(time[select_mask],np.log10(flux[select_mask]),'D',color="yellow",
                              fillstyle="none")            

        #
        # Create the extra plots for this cluster
        #  
        if alternate_plots == "Tb": 
            #
            # We cannot plot brightness temperatures
            #.  for unassigned clean components, so skip these
            #
            if i >= 0:
                #
                # Plot Tb for this cluster
                # 
                axs[1,1].plot(time,np.log10(tb_obs),color=cval,
                                marker=marker_style, fillstyle=fill_style, 
                                ls=line_style, zorder=zorder)
                axs[1,1].plot(time[~use_in_fit],np.log10(tb_obs[~use_in_fit]),marker='$/$',color='k',
                               fillstyle=fill_style,zorder=zorder+0.1,ls='')
                # overlay selected comps
                if np.any(select_mask):
                    axs[1,1].plot(time[select_mask],np.log10(tb_obs[select_mask]),'D',color="yellow",
                              fillstyle="none")            

            #
            # Plot PA for this cluster
            # 
            if i > 0:  # Do not plot core for PA
                axs[1,0].plot(time,pa,color=cval,
                             marker=marker_style, fillstyle=fill_style, 
                             ls=line_style, zorder=zorder)
                axs[1,0].plot(time[~use_in_fit],pa[~use_in_fit],marker='$/$',color='k',
                                fillstyle=fill_style,zorder=zorder+0.1,ls='')
            # overlay selected comps
            if np.any(select_mask):
                axs[1,0].plot(time[select_mask],pa[select_mask],'D',color="yellow",
                                fillstyle="none")            

       #
        elif alternate_plots == "Pol":
            #
            # if we are plotting pol.
            #
            if plot_pol and i >= 0:
                axs[1,0].plot(time,np.log10(pflux),color=cval,
                            marker=marker_style, fillstyle=fill_style, 
                            ls=line_style, zorder=zorder)
                axs[1,0].plot(time[~use_in_fit],np.log10(pflux[~use_in_fit]),marker='$/$',color='k',
                                fillstyle=fill_style,zorder=zorder+0.1,ls='')
                axs[1,1].plot(time,evpa,color=cval,
                            marker=marker_style, fillstyle=fill_style, 
                            ls=line_style,zorder=zorder)
                axs[1,1].plot(time[~use_in_fit],evpa[~use_in_fit],marker='$/$',color='k',
                                fillstyle=fill_style,zorder=zorder+0.1,ls='')

    #
    # Add labels and limits to the plots
    #
    axs[0,0].set_ylabel("Distance from Origin [mas]")
    axs[0,0].set_xlabel("Epoch")
    axs[0,1].set_ylabel("Flux Density (log10) [ Jy ]")
    axs[0,1].set_xlabel("Epoch")
    max_logflux = np.nanmax(np.log10(cluster_epoch_df['iflux']))
    axs[0,1].set_ylim(np.max([max_logflux-4,-3.5]),max_logflux+0.5)
                
    if alternate_plots == "Tb":
        axs[1,0].set_ylabel("Comp. Position Angle (deg)")
        axs[1,0].set_xlabel("Epoch")
        axs[1,1].set_ylabel("Obs. Tb (log10) [ K ]")
        axs[1,1].set_xlabel("Epoch")
        axs[1,1].set_aspect("auto")
        axs[1,1].text(0.5,0.05,"Assumes U-band,z={0:.3f}".format(z),fontsize=8,horizontalalignment='center',
                        verticalalignment='top', transform=axs[1,1].transAxes)
    elif alternate_plots == "Pol":
        axs[1,0].set_ylabel("Polarized Flux (log10) [ Jy ]")
        axs[1,0].set_xlabel("Epoch")
        axs[1,1].set_ylabel("EVPA [deg]")
        axs[1,1].set_xlabel("Epoch")
        axs[1,1].set_aspect("auto")
 
    if alternate_plots=="Speed" or six_panel:
        # set up axes depending on type of plot
        if not(six_panel):
            axs_left = axs[1,0]
            axs_right = axs[1,1]
        else:
            axs_left = axs[2,0]
            axs_right = axs[2,1]
        #    
        # Plot speed vs. distance
        #
        for j in range(len(speeds)):
            color_label = cl_colors[id_val[j]%(len(cl_colors))]
            marker_style = cl_markers[id_val[j]%(len(cl_markers))]
            fill_style = cl_fill[id_val[j]%(len(cl_markers))]
            axs_left.plot(median_dist[j],speeds[j],color=color_label,
                            marker=marker_style, fillstyle=fill_style)

        axs_left.set_xlabel("Distance from Origin [mas]")
        axs_left.set_ylabel("Apparent Speed [mas/yr]")
        #
        # Create vector plot of speed and direction
        #
        # Attempt to scale vector appropriately to plot area *and* speed...
        #  --> this is non-trivial to get good defaults for most cases
        #
        med_speed = np.nanmedian(speeds)
        maxspan = np.sqrt((ylims[0]-ylims[1])**2+(xlims[0]-xlims[1])**2)
        wspan = np.sqrt((xlims[0]-xlims[1])**2)
        vector_scale = 16*med_speed*wspan/maxspan #1.5*np.median(speed)/np.median(rpos)
        vector_width = 0.01*maxspan/wspan
        
        # Loop over clusters and plot individually using the same scale
        for j in range(len(speeds)):
            color_label = cl_colors[id_val[j]%(len(cl_colors))]
            axs_right.quiver(median_X[j],median_Y[j],
                   -speedX[j],speedY[j],
                   scale_units='width',
                   width=vector_width, 
                   scale=vector_scale,
                   color=color_label)
        axs_right.set_xlabel("X [mas]")
        axs_right.set_ylabel("Y [mas]")
        axs_right.set_aspect("equal")
        # set axes limits
        if len(xlims) > 0:
            axs_right.set_xlim(xlims)
        if len(ylims) > 0:
            axs_right.set_ylim(ylims)

    fig.legends = []  # Clear the legend

    fig.legend(loc='outside right upper',
                ncols=1+np.floor(robust_count/30),
                labelspacing=0.2,columnspacing=0.2, handletextpad=0.5) 
    
    return fig, axs                    


#------------------------------------------------------ 
# generate_random_points
#------------------------------------------------------ 
# This function generates random clusters of point components which move together over time.  These
#   are designed to be similar to clean components in that they are binned into pixels of a specified
#   size and given flux based on how many contribute to a particular bin.
# 
# cluster_data input must have the correct format....
#
#cluster_datatype = np.dtype(dtype = {'names':('epoch','centX','centY','dcentX','dcentY',
#                                              'slopeX','slopeY','dslopeX','dslopeY',
#                                              'accelX','accelY','daccelX','daccelY','medianFlux',
##                                              'sizeMaj','sizeMin','sizePA','label'),
#                            'formats':('f8','f8','f8','f8','f8','f8','f8','f8',
#                                       'f8','f8','f8','f8','f8','f8','f8','f8',
#                                       'f8',,'i') })
#
# Returns results in a format that matches the clean component / cluster data
#  dtype used in other functions...
#
#  dtype = {'names':('epoch','x','y','flux','sizex','sizey','group'),
#           formats':('f8','f8','f8','f8','f8','f8','i') })
#
def generate_random_points(Nclusters, epoch_list, pixel_size, cluster_data, min_cc_flux = 0.0002):
    
    # Do some quick sanity checks on inputs to be sure the cluster_data is in the
    #   correct format...
    if cluster_data.dtype != cluster_datatype: 
        print("Cluster Data not in correct format")
        return 0
    if len(cluster_data) < Nclusters:
        print("Too few clusters in cluster_data")
        return 0


    # Create "tiled" versions of each cluster variable to be sure we correctly account for
    #  all epochs... essentially each cluster will be repeated for each epoch in the epoch list
    centX = np.tile(cluster_data['centX'], len(epoch_list))
    centY = np.tile(cluster_data['centY'], len(epoch_list))
    slopeX = np.tile(cluster_data['slopeX'], len(epoch_list))
    slopeY = np.tile(cluster_data['slopeY'], len(epoch_list))
    ref_epoch = np.tile(cluster_data['epoch'], len(epoch_list))
    Flux = np.tile(cluster_data['medianFlux'], len(epoch_list))
    slopeLogF = np.tile(cluster_data['slopeLogF'], len(epoch_list))
    sizePA = np.tile(cluster_data['sizePA'], len(epoch_list))
    sizeMaj = np.tile(cluster_data['sizeMaj'], len(epoch_list))
    sizeMin = np.tile(cluster_data['sizeMin'], len(epoch_list))
    slopeLogsize = np.tile(cluster_data['slopeLogsize'], len(epoch_list))
    
    # Note the epochs have to be replicated cluster times for this to work...
    epochs = np.array([])
    epoch_info = np.array([],dtype=epoch_datatype) 
    for single_epoch in epoch_list:
        epochs = np.append(epochs,np.tile(single_epoch,len(cluster_data)))
        epoch_info = np.append(epoch_info, np.array([("{0}".format(single_epoch), 
                                                      single_epoch,'u',None,None,
                                                     None,None,0.7,0.7,0,
                                                     pixel_size)], dtype=epoch_datatype))
    
    # translate cluster_data into centers, fluxes, sizes, etc...
    # -- Note, sizes are computed below due to need to create a correlation
    #    matrix for each cluster epoch one at a time.
    cx = centX+slopeX*(epochs-ref_epoch)
    cy = centY+slopeY*(epochs-ref_epoch)
    fluxes = Flux*np.exp(slopeLogF*(epochs-ref_epoch))
    Major = (sizeMaj*np.exp(slopeLogsize*(epochs-ref_epoch)))
    Minor = (sizeMin*np.exp(slopeLogsize*(epochs-ref_epoch)))
    # for the component PA, need to compute rotation angle for (x,y) coord expected
    #   and convert to radians...
    Rotate = (-sizePA+90)*np.pi/180  
    
    
    
    # Loop over the individual clusters and generate
    # positions for that cluster at each of the epochs
    #

    # Compute the number of clean components for each cluster based on the flux
    cc_number = fluxes/min_cc_flux
    
    #  Build a list of times and positions for "clean components", which 
    #  are just (x,y) points randomly found within some probabilistic distance of 
    #  the center of each cluster.  
    #
    t = np.array([])
    x = np.array([])
    y = np.array([])
    for i in range(len(cc_number)):
        # Convert ellipse dimensions into an appropriate correlation matrix between x and y, assuming
        #   a SKY coordinate system for the definition of the PA (Rotation angle computed above)
        #  We need to create a rotated size matrix and then "square" it...
        #    e.g. (Rotation * size matrix) x (Rotation * size matrix).T 
        size_matrix_rot = np.array([[Major[i]*np.cos(Rotate[i]),-Minor[i]*np.sin(Rotate[i])],
                                    [Major[i]*np.sin(Rotate[i]), Minor[i]*np.cos(Rotate[i])]])
        sig_corr_matrix = np.dot(size_matrix_rot,size_matrix_rot.T)
                              
        #print(sig_corr_matrix, Major[i], Minor[i], Rotate[i])
        #return 0
        
        # Assign t-values and associated random (x,y) component locations 
        #    using the multi-variate normal distribution to have the correct 
        #    elliptical structure
        tvals = np.full(np.int64(cc_number[i]),epochs[i])
        xyvals = np.random.multivariate_normal([cx[i],cy[i]],sig_corr_matrix,np.int64(cc_number[i]))
        
        t = np.append(t,tvals)
        x = np.append(x,xyvals[:,0])
        y = np.append(y,xyvals[:,1])
                       
    # Create binned versions to represent clean components with fluxes represented by the
    #  number of times those unique points show up
    xbin = pixel_size*np.floor((x/pixel_size)+0.5)
    ybin = pixel_size*np.floor((y/pixel_size)+0.5)

    # Create an array representing (time,xbin,ybin) for each component, then find the replicates of
    #  each of these cases... keep only one of each, but make the count of the replicates = flux.
    points = np.array([t,xbin,ybin])
    unique_points, unique_counts = np.unique(points,return_counts=True,axis=1)
            
    # Return results in a format that matches the clean component / cluster data 
    #  dtype used in other functions...
    #
    #  dtype = {'names':('epoch','x','y','flux','sizex','sizey','group'),
    #           formats':('f8','f8','f8','f8','f8','f8','i') })
    
    sim_data = np.zeros(len(unique_points[0]), dtype = cc_datatype)
          
    sim_data['epoch'] = unique_points[0]
    sim_data['x'] = unique_points[1]
    sim_data['y'] = unique_points[2]
    sim_data['flux'] = unique_counts*min_cc_flux
    sim_data['group'] = 0
    
    return sim_data, epoch_info
        



#------------------------------------------------------
# plot_xy_data 
#------------------------------------------------------ 
#
# Function to plot (x,y) positions of clean components
# in a specified epoch
#
def plot_xy_data(epoch, data,xrange=None,yrange=None):
    epoch_data = data[data['epoch']==epoch] 
    plt.scatter(epoch_data['x'],epoch_data['y'])    
    plt.axis("equal")  # Must do this before setting xrange and yrange!    
    if xrange is not None and yrange is not None:
        plt.xlim(xrange)
        plt.ylim(yrange)
    # For SKY plotting, invert the xaxis
    plt.gca().invert_xaxis()

    plt.title(str(epoch))

    fig.show()


#------------------------------------------------------ 
# gen_multi_comp
#------------------------------------------------------ 
#
#  Function to simplify the generation of simulated moving cluster data
# 
#  The idea is that not all of these parameters need to be defined each 
#  time, see the line below this block to see how this could be used to
#  quickly generate results
#
#  Returns a clean component list in the appropriate data-format with
#  multiple epochs, similar to a real dataset
#
def gen_multi_comp(fluxes=None,
                   sizes=None,  
                   xpos=None,
                   ypos=None,
                   slopeX=None,
                   slopeY=None,
                   ep_list=None,
                   ref_ep=2001.9,
                   plot_data=True,
                   pixel_size=0.1,
                   min_cc_flux=0.0002):
    
    # Set default parameters if none provided
    if fluxes is None:
        fluxes = np.array([1.0,0.1,0.1,0.01,0.01])
    if sizes is None:
        sizes = np.array([0.1,0.25,0.25,0.5,0.5])
    if xpos is None:
        xpos = np.array([0,-2.0,-3.0,-6.0,-8.0])
    if ypos is None:
        ypos = np.array([0,0,0,0,0])
    if slopeX is None:
        slopeX = np.array([0,0,0,0,0])
    if slopeY is None:
        slopeY = np.array([0,0,0,0,0])
    if ep_list is None:
        ep_list = np.array([2001.0, 2001.5, 2001.9, 2002.3, 2002.5])    
    #
    # Compute the number of clusters
    cluster_num = len(fluxes)
    #
    # Check that we provided enough parameters for clusters
    if len(xpos)+len(ypos)+len(sizes)+len(slopeX)+len(slopeY) != 5*cluster_num:
        print("Incorrect number of clusters parameters given!")
        return

    #
    # Create a cluster datatype object for simulated clusters
    sim_cluster_data = np.zeros(cluster_num,dtype=cluster_datatype)
    #
    # Each of the following arrays has one value per cluster
    #
    sim_cluster_data['medianFlux'] = fluxes         # fluxes in Jy
    sim_cluster_data['centX'] = xpos
    sim_cluster_data['centY'] = ypos
    sim_cluster_data['sizeMaj'] = sizes           
    sim_cluster_data['sizeMin'] = sizes
    sim_cluster_data['slopeX'] = slopeX
    sim_cluster_data['slopeY'] = slopeY    
    sim_cluster_data['epoch'] = np.tile(ref_ep,cluster_num)
    #
    # Now generate data
    #
    multi_comp_data=generate_random_points(cluster_num,ep_list,pixel_size,sim_cluster_data,min_cc_flux)
    #
    # Plot data?
    #
    if plot_data:
        # Set multi-epoch limits to all be the same
        xmin = np.min(multi_comp_data['x'])
        xmax = np.max(multi_comp_data['x'])
        xspread = xmax-xmin
        xmax += 0.05*xspread
        xmin -= 0.05*xspread
        ymin = np.min(multi_comp_data['y'])
        ymax = np.max(multi_comp_data['y'])
        yspread = ymax-ymin
        ymax += 0.05*yspread
        ymin -= 0.05*yspread
        
        # Plot each epoch in turn
        for epoch in ep_list:
            plot_xy_data(epoch,multi_comp_data,[xmin,xmax],[ymin,ymax])
    #        
    # Return results
    return multi_comp_data


#------------------------------------------------------ 
#------------------------------------------------------ 

#
#  Code for overplotting component positions on maps in individual epochs
#

#  Main plotting code from make_MOJAVE_plots.py -- v2025_05_01
#
#   --> modified a bit for the purposes here, but goal is to keep it
#       close enough that we can easily update it in the future.

#-------------------------------------------------------------------
# Import Libraries Needed
#
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

#import lic

#plt.rcParams['figure.dpi'] = 300
plt.rcParams['contour.linewidth'] = 0.75

#
# Function to make a mojave style image plot based on inputs
#         
#  ifits = FITS image loaded with fits.open(filename)[0]
#  qfits = ...    None for either q or u defaults to Stokes-I plot only
#  ufits = ...     
#
#  inoise, qnoise, unoise.   Noise levels, get from image if = None. 
#
#  lims = [x1,x2,y1,y2] positions of image corners in mas coordinates
#  iptype = 'Color', 'Contour', 'Both'.  # Only active when q and u not provided
#. pptype = 'Fpol', 'Ppol'               # polarization plot type
#
#  ibase = I base contour level
#. ibase_factor = factor * inoise for determining above if ibase not given
#  iccolor = I contour color  (defaults = black for contours only or pol. image,
#                                       = white when overplotted on color I image
#  icalpha = I contour alpha
#. icut = cut level for making Frac. Pol and Chi maps
#. icut_factor = factor * inoise for determine above if icut not given
#
#. ... same as above for pbase, etc. but for P contours
#
#  cstep = multiplicate factor for all contours
#
#  cmap = colormap for either Stokes-I (if iptype = 'Color' or 'Both')
#.        OR polarization (if qfits and ufits provided)
#. linthresh = absolute scale at which cmap becomes linear (Stokes-I plot)
#               None defaults to 6 * inoise
#  vmax = max of color brightness scale (0 = use datamax, or 0.7 for frac. pol)
#  vmin = min of color brightness scale (0 = use datamin, or 0.0 for frac. pol)
#
#  axes = "Affine" gives a simple square grid with relative mas positions
#         "WCS" gives RA and Dec coordinates
#
#. beam plot assumes stokes I beam is same for all.
#
def plot_image(ifits, qfits=None, ufits=None, 
                   inoise=None, qnoise=None, unoise=None, pnoise=None,
                   lims=None, make_square=True,      # plotting limits, plus flag to expand them to square as needed
                   evpa_rot=None,
                   iptype = 'Both',                  # iptype is only for Stokes-I only plots
                   pptype = 'Fpol',
                   
                   ibase_factor = 3.0, pbase_factor = 3.0,    # Multiple of corresponding noise 
                   iccolor=None, icalpha=0.70,                  # I contour properties
                   pccolor='blue', pcalpha=0.70,                # P contour properties 
                   cstep = 2.0,
                   
                   icut_factor=5.0, pcut_factor=5.0,            # cut values for Frac. Pol
                   
                   tick_len=None, tick_sep=None,                # tick parameters, None = estimate
                   tick_color=None,
                   cmap=None, linthresh=None, vmax=0, vmin=0,   # color plot parameters
                   tcmap=cm.gnuplot2_r,
                   axes = 'Affine', 
                   show_grid = False,                           # overplot a grid
                   xshift = 0.0, yshift = 0.0,                  # amount to shift map centroid when plotting
                   correct_Rician_Bias = True,
                   sname=None,
                   epoch=None,
                   start_year=1994.0,                           # default start and end years for stacking axis
                   end_year=2026.0,
                   plot_beam = True,
                   plot_info = False,
                   show_colorbar = True,
                   remove_text = False,
                   taper = False, stacked=False,
                   fig=None, ax=None):
    
    
    #----------------------------------------------
    # Set istokes_type
    #
    istokes_type = "I"
    if 'CTYPE4' in ifits.header and 'STOKES' in ifits.header['CTYPE4'] and not(stacked):
        if ifits.header['CRVAL4'] == -1:
            istokes_type = "RR"
        if ifits.header['CRVAL4'] == -2:
            istokes_type = "LL"
    
    # ---------------------------------------------
    # Assign noise estimates if not given
    #
    if inoise is None:
        print("Stokes I:")
        inoise, imap_noise, itail_noise = get_noise_estimates(ifits, ifits)  
    if pnoise is None and not(qfits is None or ufits is None):
        if qnoise is None and qfits is not None:
            print("Stokes Q:")
            qnoise, qmap_noise, qtail_noise = get_noise_estimates(qfits, ifits)  
        if unoise is None and ufits is not None:
            print("Stokes U:")
            unoise, umap_noise, utail_noise = get_noise_estimates(ufits, ifits)
        #pnoise = (qnoise + unoise)/2
        #   The expression above only works if qnoise=unoise, but works for all P.  
        #   The expression below will give the same answer in the qnoise = unoise case,
        #   but should work more generally in noise dominated cases where qnoise or unoise is 
        #   significantly larger than the other one.  Here I've assumes q = qnoise and 
        #   u = unoise to get this result... 
        pnoise = np.sqrt((qnoise**4+unoise**4)/(qnoise**2+unoise**2))

    # --------------------------------------------
    # Find plotting limits if none are given
    #  
    if lims is None:
        lims=find_box(ifits,inoise,make_square,stacked=stacked)
        lims[0] += xshift
        lims[1] += xshift
        lims[2] += yshift
        lims[3] += yshift
        
    # --------------------------------------------------
    # Setup polarization plotting parameters, if needed
    #
    if not(qfits is None or ufits is None):

        # Set tick_len and tick_sep if None,
        #.  Use the pixel size to help set the tick_sep scale, which is pixel based
        maxsize = np.max([np.abs(lims[0] - lims[1]),np.abs(lims[3] - lims[2])])
        tick_scale = (ifits.header['CDELT2']*(60*60*1000))/(0.1) 
        #tick_scale = (15.4e9/ifits.header['CRVAL3'])
        if tick_len is None:
            tick_len = 0.4 * np.sqrt(maxsize*tick_scale/20)
        if tick_sep is None:
            tick_sep = int(4.0 * np.sqrt(maxsize/(20*tick_scale)) + 0.5)

        #    
        #  Construct polarization quantities  
        #
        Pdata = np.sqrt((get_image_data(qfits))**2 + (get_image_data(ufits))**2)
        #
        if correct_Rician_Bias:
            Pdata_sqr = (Pdata**2 - pnoise**2)
            Pdata_sqr[Pdata_sqr < 0.0] = 0.0
            Pdata = np.sqrt(Pdata_sqr)
            if show_colorbar:            # don't print message for subsequent frames of a movie
                print("Correcting Rician Bias in Calculated P")
        # handle Idata carefully when dividing to make Mdata
        #. --> set all points <= 0 to NaN
        Idata_divisor = get_image_data(ifits).copy()
        Idata_divisor[Idata_divisor <= 0.0] = np.nan
        Mdata = Pdata/Idata_divisor
        #
        Xdata = 0.5*np.arctan2(get_image_data(ufits),get_image_data(qfits))

        # Apply cutoffs to Mdata and Xdata
        icut = icut_factor*inoise
        pcut = pcut_factor*pnoise
        evpa_cut = pbase_factor*pnoise

        Mdata[(Pdata < pcut) | (get_image_data(ifits) < icut)] = np.nan
        if pptype == "Fpol":
            Xdata[(Pdata < pcut) | (get_image_data(ifits) < icut)] = np.nan
        else:
            Xdata[(Pdata < evpa_cut)] = np.nan

        # Add a rotation if needed    
        if evpa_rot is not None:
            Xdata += evpa_rot*np.pi/180.0
            
        # Set flag for making polarization plot
        #print("Polarization images provided, defaulting to pol. plot")
        make_pol_plot = True
        
    else:
        #print("Polarization images not provided, defaulting to Stokes I plot")
        make_pol_plot = False
        
    # --------------------------------------------
    # Create plotting surface
    #
    if fig is None:
        fig = plt.figure()

    # --------------------------------------------
    # Setup unit conversions for plotting
    #

    # pixels to mas conversion
    #  -- used for Affine transformation (relative mas coordinates)
    #     and for computing plotting limits relative to center in
    #     mas units.
    pix_to_mas_x = ifits.header['CDELT1']*(60*60*1000)
    pix_to_mas_y = ifits.header['CDELT2']*(60*60*1000)

    #
    # Choose between WCS coordinates and a simpler Affine transform
    # 
    if axes == 'WCS' and ax is None:
        # Use WCS coordinates in degrees
        wcs = WCS(ifits.header)
        ax = fig.add_subplot(projection=wcs, slices=('x','y',0,0))
        
        ax.coords[0].set_axislabel('Right Ascension')
        ax.coords[1].set_axislabel('Declination')
        ax.coords[0].set_ticklabel(exclude_overlapping=True)
        ax.coords[1].set_ticklabel(exclude_overlapping=True)
        
        if xshift != 0.0 or yshift != 0.0:
            print("Cannot execute xshift or yshift with WCS coordinates, use Affine")
            return None, None
            
    elif axes == 'Affine' and ax is None:
        # Set up an affine transformation
        transform = Affine2D()
        transform.scale(pix_to_mas_x,pix_to_mas_y)
        transform.translate(-(ifits.header['CRPIX1']-1)*pix_to_mas_x+xshift, 
                            -(ifits.header['CRPIX2']-1)*pix_to_mas_y+yshift)
        transform.rotate(0)  # radians

        # Set up metadata dictionary
        coord_meta = {}
        coord_meta['name'] = 'Relative RA', 'Relative Dec'
        coord_meta['type'] = 'scalar', 'scalar'
        coord_meta['wrap'] = None, None
        coord_meta['unit'] = u.mas, u.mas
        coord_meta['format_unit'] = None, None
    
        # Create figure axes with transformation defined above
        ax = WCSAxes(fig, [0.12,0.12,0.78,0.78], aspect='equal',
                 transform=transform, coord_meta=coord_meta)
        fig.add_axes(ax)
        
    elif ax is None:
        # No coordinate transformation specified
        print("No coordinate transformation given.")
        return 0
    else:
        # clear what was here before
        for artist in ax.lines + ax.collections+ax.patches+ax.images: #ax.texts -- keep labels!
            artist.remove()
        if remove_text:
            for artist in ax.texts:    # -- remove labels!
                artist.remove()
    #
    # Set axes aspect ratio to be equal
    #
    ax.set_aspect("equal")

    # - warn and exit if for some reason the aspect should not be equal...
    if(np.abs(ifits.header['CDELT1']) != np.abs(ifits.header['CDELT2'])):
        print("Pixels not equally sized! Assumptions violated!")
        print(ifits.header['CDELT1'], ifits.header['CDELT2'])
        return 

    # set plotting limits, using mas based relative to center
    if np.any(np.array(lims) != 0):
        # translate to pixel coord first
        x1 = ifits.header['CRPIX1'] - 1 + lims[0]/pix_to_mas_x - xshift/pix_to_mas_x 
        x2 = ifits.header['CRPIX1'] - 1 + lims[1]/pix_to_mas_x - xshift/pix_to_mas_x 
        y1 = ifits.header['CRPIX2'] - 1 + lims[2]/pix_to_mas_y - yshift/pix_to_mas_y
        y2 = ifits.header['CRPIX2'] - 1 + lims[3]/pix_to_mas_y - yshift/pix_to_mas_y
        # set limits
        ax.set_xlim(x1,x2)
        ax.set_ylim(y1,y2)
    else:
        expand_lims = False

    
    # have axis ticks pointing inward
    ax.tick_params(direction="in")
    
    # add a grid to define axes a little more clearly
    if show_grid:
        ax.grid()

    #--------------------------------------------
    # Create Stokes I plot(s)
    #   
    # use color for I if requested, but not if a Pol. plot
    im = None
    #
    if make_pol_plot == False and (iptype == 'Both' or iptype == 'Color'):
        #
        # default color map if none is given
        if cmap is None:
            cmap = cm.inferno
        #
        # define linthresh at which Stoke-I scale becomes linear
        if linthresh is None:
            linthresh = 6.0*inoise
        #
        # set plotting limits
        if vmax == 0: 
            vmax = np.max(get_image_data(ifits))
        if vmin == 0:
            vmin = np.min(get_image_data(ifits))
            vmin = np.min([vmin, -linthresh*1.01])
        #
        # Don't show color for negative Stokes-I for stacked images
        #
        if stacked:
            vmin = 0
        #
        # plot color image
        #.  --use SymLogNorm only if linthresh is low enough
        #       that it makes sense.  Otherwise use linear
        if linthresh < 0.5*np.max([vmax,-vmin]):
            im = ax.imshow(get_image_data(ifits),
                           origin='lower',
                           cmap=cmap,
                           norm=mcolors.SymLogNorm(linthresh=linthresh,
                                                   vmax=vmax,vmin=vmin))
        else:
            im = ax.imshow(get_image_data(ifits),
                           origin='lower',
                           cmap=cmap,
                           vmax=vmax, vmin=vmin) 
        if show_colorbar:          
            cb = fig.colorbar(im, pad=0.01)
            cb.set_label(label="Stokes {0} Intensity [Jy/bm]".format(istokes_type),size='x-small')
            cb.ax.tick_params(labelsize='x-small')

    # find the image max, positive or negative
    max_val = np.max(np.abs(get_image_data(ifits)))
    neg_max = np.max(-get_image_data(ifits))
    if neg_max == max_val:
        max_val = -neg_max
        
    # add contours if needed
    if make_pol_plot or iptype == 'Both' or iptype == 'Contour':
        # define contour levels
        ibase = ibase_factor*inoise
        #
        icmax = np.abs(max_val)
        iclevs = np.array([-ibase,ibase])
        while iclevs[-1] < icmax/cstep:
            iclevs = np.append(iclevs, [-iclevs[-1]*cstep,iclevs[-1]*cstep])
        iclevs = np.sort(iclevs)
        #print("Contour levs = ", clevs)
        
        # define contour color
        if iccolor is None:
            if iptype == 'Both' and not(make_pol_plot):
                iccolor = 'white'
            else: 
                iccolor = 'black'
        # show contours
        ax.contour(get_image_data(ifits),levels=iclevs, colors=iccolor, alpha=icalpha)

    #--------------------------------------------
    # Create Pol. aspects of plot, if needed
    #   
    if make_pol_plot:

        if pptype == "Fpol":
            #
            # plot a color image for fractional polarization
            #
            # plot color image
            #.  --use SymLogNorm only if vmax is high enough
            #       that it makes sense.  Otherwise use linear
            #
            # set fractional pol. plotting limits and colors
            if vmax == 0 and vmin == 0: 
                if np.isnan(Mdata).all():
                    max_Mdata = 0
                else:
                    max_Mdata = np.nanmax(Mdata)
                vmax = np.max([0.1, np.min([0.8, max_Mdata*.8/.7])])
                vmin = 0
                # record frange if this is run as a script...
                if __name__ == "__main__": config['frange'] = vmax
            # default color map if none is given
            if cmap is None:
                cmap = cmaps.neon_r
            if tick_color is None:
                tick_color = "black"

            if vmax > 0.4:
                im = ax.imshow(Mdata,
                               origin='lower',
                               cmap=cmap,
                               norm=mcolors.SymLogNorm(linthresh=0.4,
                                                   vmax=vmax,vmin=vmin))
            else:
                im = ax.imshow(Mdata,
                               origin='lower',
                               cmap=cmap, 
                               vmax=vmax, vmin=vmin)
            if vmax >= 0.4:
                ticks = np.array([0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4])
                ticklabels = ["0.0", "", "0.1", "", "0.2", "", "0.3", "", "0.4"]
                # add appropriate ticklabels up to 0.7.
                for tickval in [ "0.5", "0.6", "0.7" ]:
                    if vmax >= float(tickval):
                        ticks = np.append(ticks, float(tickval))
                        ticklabels.append(tickval)
                if show_colorbar:
                    cb = fig.colorbar(im, location='right',pad=0.01,ticks=ticks)
                    cb.ax.set_yticklabels(ticklabels)  
            elif show_colorbar:
                cb = fig.colorbar(im, location='right',pad=0.01)
            # 
            if show_colorbar:   
                cb.set_label(label="Fractional Linear Polarization",size='x-small')
                cb.ax.tick_params(labelsize='x-small')
            #
            plot_type_text = "Frac. Pol."            
        
        elif pptype == "Ppol":
            #
            # plot a color image for polarized flux
            #
            # plot color image
            #.  --use SymLogNorm only if vmax is high enough
            #       that it makes sense.  Otherwise use linear
            #
            # default color map if none is given
            if cmap is None:
                cmap = cm.cubehelix_r
            if tick_color is None:
                tick_color = "white"
            #
            # define linthresh at which Stoke-I scale becomes linear
            if linthresh is None:
                linthresh = 10.0*pnoise
            #
            # set plotting limits
            if vmax == 0: 
                vmax = np.max([np.max(Pdata),10.0*pnoise])
            if vmin == 0:
                vmin = 0 #np.min(Pdata)
            #
            # plot color image
            #.  --use SymLogNorm only if linthresh is low enough
            #       that it makes sense.  Otherwise use linear
            if linthresh < 0.5*np.max([vmax,-vmin]):
                im = ax.imshow(Pdata,
                               origin='lower',
                               cmap=cmap,
                               norm=mcolors.SymLogNorm(linthresh=linthresh,
                                                   vmax=vmax,vmin=vmin))
            else:
                im = ax.imshow(Pdata,
                               origin='lower',
                               cmap=cmap,
                               vmax=vmax, vmin=vmin)
            if show_colorbar:           
                cb = fig.colorbar(im, pad=0.01)
                cb.set_label(label="Linear Polarized Intensity [Jy/bm]",size='x-small')
                cb.ax.tick_params(labelsize='x-small')
            #
            plot_type_text = "EVPA"
            
        #
        # Create and plot tick marks showing EVPA
        #
        xv,yv = np.meshgrid(np.arange(0, len(Xdata)),np.arange(0,len(Xdata)))
        skip = (slice(None, None, tick_sep), slice(None, None, tick_sep))
        #
        # if tick color is based on fractional polarization, set up colorscheme
        #
        if tick_color == "Fpol":
            # set fractional pol. plotting limits and colors
            if np.isnan(Mdata).all():
                max_Mdata = 0
            else:
                max_Mdata = np.nanmax(Mdata)
            fmax = np.max([0.1, np.min([0.8, max_Mdata*.8/.7])])
            fmin = 0
            #    
            evpa = ax.quiver(xv[skip],yv[skip],-tick_len*np.sin(Xdata[skip]), 
                      tick_len*np.cos(Xdata[skip]), Mdata[skip],cmap=tcmap, #cm.spring_r, #cm.gnuplot2_r,
                      norm=mcolors.SymLogNorm(linthresh=np.min([0.4,fmax]),vmax=fmax,vmin=fmin),
                      angles='xy', scale_units='xy', scale=pix_to_mas_y, width=0.0035,
                      headaxislength=0,headwidth=0,headlength=0,pivot='middle',alpha=1.0,zorder=2.5)
            #
            if fmax >= 0.4:
                ticks = np.array([0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4])
                ticklabels = ["0.0", "", "0.1", "", "0.2", "", "0.3", "", "0.4"]
                # add appropriate ticklabels up to 0.7.
                for tickval in [ "0.5", "0.6", "0.7" ]:
                    if fmax >= float(tickval):
                        ticks = np.append(ticks, float(tickval))
                        ticklabels.append(tickval)
            else:
                ticks = np.array([0.0])
                ticklabels = ["0.0"]
                # add appropriate ticklabels up to 0.7.
                for tickval in [ "0.05", "0.1", "0.15", "0.2","0.25", "0.3", "0.35" ]:
                    if fmax >= float(tickval):
                        ticks = np.append(ticks, float(tickval))
                        ticklabels.append(tickval)
            if show_colorbar:
                cb2 = fig.colorbar(evpa, location='right',pad=0.01,ticks=ticks)
                cb2.ax.set_yticklabels(ticklabels)  
                cb2.set_label(label="Fractional Polarization",size='x-small')
                cb2.ax.tick_params(labelsize='x-small')
        else:
            ax.quiver(xv[skip],yv[skip],-tick_len*np.sin(Xdata[skip]), 
                  tick_len*np.cos(Xdata[skip]), color=tick_color,
                  angles='xy', scale_units='xy', scale=pix_to_mas_y, width=0.0035,
                  headaxislength=0,headwidth=0,headlength=0,pivot='middle', zorder=2.5)
            
    # 
    # Changes for a stacked image
    #
    if stacked:
        # count the number of epochs in the stack
        epoch_count = 0
        epoch_jyear = np.array([])
        while epoch_count < 1000:
            key = "EPCH{0}".format(epoch_count+1)     
            epoch_val = ifits.header[key]
            if epoch_val is None:
                break
            #print(epoch_val)
            epoch_jyear = np.append(epoch_jyear,Time(epoch_val.replace('_','-')).jyear)
            epoch_count = epoch_count + 1
            
        if epoch_count == 1000:
            print("WARNING: epoch count is capped at 1000, fix code!")
        
        #
        # Change 'epoch' label to an indication that this is stacked
        #
        epoch = "{0} Epoch Stack".format(epoch_count) 

        #
        # Add an inset for a stacked image
        #
        axins = ax.inset_axes(
                    [0.35, 0.89, 0.63, 0.05],
                    ylim=(0, 1), xlim=(start_year, end_year), 
                    yticklabels=[])
        #axins.set_frame_on(False)
        if make_pol_plot:
            tcolor = 'k'
        else:
            tcolor = 'w'
        axins.tick_params(labelsize=6, color=tcolor,left=False,labelcolor=tcolor,length=2)
        for year_val in epoch_jyear:
            axins.axvline(year_val,0,1,linewidth=0.3)        
    
    #
    # Plot info along top of plot
    #
    if plot_info:
        #
        # Basic image statistics
        #
        if make_pol_plot:
            stat_text = "{0}, {1} = {2:0.1f}, {3:0.1f} and ".format("$I_{peak}$","$P_{peak}$",
                                                                       np.max(get_image_data(ifits))*1000.0,
                                                                       np.max(Pdata)*1000.0)
            stat_text += "{0}, {1} = {2:0.2f}, {3:0.2f} mJy/bm".format("$I_{noise}$","$P_{noise}$",
                                                                     inoise*1000.0,pnoise*1000.0)
            stat_text += "\n{0} = {1:0.2f} [x{2} steps] mJy/bm".format("$I_{cont}$",ibase*1000.0,cstep)   
    
            stat_text += ", {0} for ".format(plot_type_text)
            if pptype == "Fpol":                                            
                stat_text += "{0} {1:0.2f}, {2:0.2f} mJy/bm".format("$I$, $P$ $\\geq$",
                                                                    icut*1000.0,pcut*1000.0)
            else:
                stat_text += "{0} {1:0.2f} mJy/bm".format("$P$ $\\geq$", evpa_cut*1000.0)
        else:
            stat_text = "{0} = {1:0.1f}, ".format("${0}$".format(istokes_type)+"$_{peak}$", max_val*1000.0)
            stat_text += "{0} = {1:0.2f} mJy/bm".format("${0}$".format(istokes_type)+"$_{noise}$",inoise*1000.0)
        
            if iptype == 'Both' or iptype == 'Contour':
                stat_text += ", and {0} = {1:0.2f} [x{2} steps] mJy/bm".format("${0}$".format(istokes_type)+"$_{cont}$", 
                                                                              ibase*1000.0,cstep)               

        ax.text(0.01, 1.005, stat_text, transform=ax.transAxes, size='x-small', ha='left',verticalalignment='bottom')#, bbox=props)
        
        #
        # Source Name, Band, Etc...
        #
        if sname is None:
            sname = ifits.header['OBJECT']
        if epoch is None:
            epoch = ifits.header['DATE-OBS']
        # replace '_' with '-' in epoch names
        epoch = epoch.replace('_','-')
            
        source_text = "{0}, {1}, {2} {3:0.1f} GHz".format(sname, epoch,
                                                          ifits.header['TELESCOP'],
                                                          ifits.header['CRVAL3']/1e9)
        source_text += "\n" + get_observer_string(ifits.header['OBSERVER'], sname, stacked)
        if taper:
            source_text += "\n[Tapered Image]"
        
        if not(make_pol_plot) and (iptype == 'Both' or iptype == 'Color'):
            props = dict(boxstyle='round', facecolor='black', alpha=0.0)
            font_color='white'
        else:
            props = dict(boxstyle='round', facecolor='white', alpha=0.0)
            font_color='black'
            
        ax.text(0.02, 0.98, source_text, transform=ax.transAxes, size='medium', color=font_color, ha='left',verticalalignment='top', bbox=props)
                
    if plot_beam: 
        bmaj = np.abs(ifits.header['BMAJ']/ifits.header['CDELT1'])  # convert to pixels
        bmin = np.abs(ifits.header['BMIN']/ifits.header['CDELT1'])
        bpa = ifits.header['BPA']
        
        # If we are plotting the full image, set x1, y1 just for beam
        if np.all(np.array(lims) == 0):
            x1 = 0
            y1 = 0

        beam = patches.Ellipse((x1+bmaj, y1+bmaj), bmin, bmaj,
                     angle=bpa, linewidth=0, fill=True, zorder=2, fc='grey',ec='black')


        ax.add_patch(beam)

    return fig, ax, im

#
# Function to construct a filename and retrieve a MOJAVE FITS image
#
def grab_mojave_image(sname, epoch, data_dir, band='u', stokes='i'):
    
    #
    image_file = data_dir + sname + "/" + epoch + "/"
    image_file = image_file + sname + "." + band + "." + epoch + "." + stokes + "cn.fits.gz"
    #
    cc_file = data_dir + sname + "/" + epoch + "/"
    cc_file = cc_file + sname + "." + band + "." + epoch + "." + stokes + "cn.fits.gz"
    
    print("Grabbing {0}".format(image_file))

    return fits.open(image_file)[0], cc_file

#
# Function to overplot all the clusters in a given epoch
#
def overplot_clusters(epoch_info_arr, data, root_data_dir,
                   cluster_epoch_df, labels, epoch_int,
                   xshift=None, yshift=None, offset=None,
                   lims=None, cbase=None, cstep=2.0,
                   ptype = 'Contour', 
                   cmap=cm.viridis, linthresh = 1e-3, vmax=0, vmin=-1e-99,
                   fig=None, ax=None, cb=None, axes='Affine'):
    #
    if lims is None:
        lims = [ 0, 0, 0, 0 ]
        
    # select specific values for this particular epoch
    epoch_info = epoch_info_arr[epoch_int]
    #print(epoch_info)
    epoch = epoch_info['epoch_val']
    epoch_mask = (cluster_epoch_df['epoch']==epoch)
    id_mask = (cluster_epoch_df['clusterID']>=0)  # include only fitted clusters
    cl_ep_df = cluster_epoch_df[epoch_mask*id_mask]
    Nclusters = len(cl_ep_df)
    # Lists of current cluster IDs (used in plotting) 
    #  and original IDs (used in ccdata identification) assigned when the model was fit
    clusterID_list = np.array(cl_ep_df['clusterID'])
    origID_list = np.array(cl_ep_df['origID'])
    #
    if Nclusters > 0:
        core_x = cl_ep_df.iloc[0].loc['core_x']
        core_y = cl_ep_df.iloc[0].loc['core_y']
    else:
        core_x = 0.0
        core_y = 0.0    
    
    # Get noise and base contours on that...
    if cbase is None:
        cbase = 3.5*epoch_info['inoise']

    # Load image
    image = fits.open(root_data_dir+epoch_info['fits_file'])[0]
    # pixels to mas conversion
    pix_to_mas_x = image.header['CDELT1']*(60*60*1000)
    pix_to_mas_y = image.header['CDELT2']*(60*60*1000)
    pix_to_mas = np.abs(pix_to_mas_x)
    # shift to put core position in center -- be careful with artifacts, might need to use np.rint to avoid interpolation artifacts, but this can cause issues with very small pixel sizes and large shifts.  In those cases, the shift should be small enough that the artifacts are not a problem, so we can skip the rounding.  This is a bit hacky, but it seems to work for our data.  If you have a better solution, please let me know!
    shift_x = -core_x/pix_to_mas_x #np.rint(core_x/pix_to_mas_x)
    shift_y = -core_y/pix_to_mas_y #np.rint(core_y/pix_to_mas_y)
    image = set_image_data(image, ndimage.shift(get_image_data(image),(shift_y,shift_x),order=1,mode='nearest'))   
    #
    # Create image and return axes if we are not plotting on an existing figure
    #
    fig, ax, im = plot_image(image, lims=lims, iptype = ptype, 
                         inoise=epoch_info['inoise'],
                         ibase_factor=cbase/epoch_info['inoise'], cstep=cstep,
                         cmap=cmap, linthresh=linthresh, show_colorbar=False, 
                         vmax=vmax, vmin=vmin, remove_text=True,
                         axes = axes, fig=fig, ax=ax)
    if ptype == 'Color':
        #print(image.data[60:70,60:70])
        if cb is None: 
            cb = fig.colorbar(im, pad=0.01)
            cb.set_label(label="Stokes I Intensity",size='x-small')
            cb.ax.tick_params(labelsize='x-small')
        else:
            cb.update_normal(im)    

    #
    #  If there are no clusters in this epoch, just return the image
    #.   --> this can happen if the epoch falls outside the time range window
    #
    if Nclusters < 1:
        ax.set_title("Epoch {0}, cbase={1:0.2f} mJy/beam".format(epoch,1000*cbase))
        ax.text(0.5,0.1,"No clusters found for epoch {0}, check fit window!".format(epoch),
                horizontalalignment='center', verticalalignment='center',
                transform=ax.transAxes, fontsize=12)
        return fig, ax


    # Note the image plot returned above has its axes units in pixels, 
    #  so we will need to convert all X and Y mas positions and sizes
    #  to pixels
        
    #---------------------------------------------------
    #  Now setup overplotting cluster positions and sizes
    #
                   
    #
    # plot ellipses of cluster sizes computed from clean components
    #   directly.  Include centers computed from clean components. 
    #   
    #   NOTE: the second overlay plots below will feature the properties
    #         of the cluster model fits
    #. -- note that I'm not 100% sure about non-circular cases... 
    #     tried a few options to see we got the right major axis and cpa, 
    #     but also need to check when a real case comes up in the data (rare).
    core_xpos = 0.0
    core_ypos = 0.0
    # loop over clusters    
    for c in range(0,Nclusters):
        # create a reference to this cluster
        cl_info = cl_ep_df[cl_ep_df['clusterID']==clusterID_list[c]]
        # skip if there are no cc's associated with it!
        if cl_info.iloc[0].loc['N_Icc'] < 1:
            continue
        # compute position in pixels on map
        xpos = (cl_info.iloc[0].loc['avg_x']-cl_info.iloc[0].loc['core_x'])
        ypos = (cl_info.iloc[0].loc['avg_y']-cl_info.iloc[0].loc['core_y'])
        xpos = image.header['CRPIX1'] - 1 + xpos/pix_to_mas_x
        ypos = image.header['CRPIX2'] - 1 + ypos/pix_to_mas_y
        # find core_xpos, core_ypos for reference *if* the origID == 0 and clusterID != 0
        #. --> allows reasonable plotting of cluster model positions relative to new clusterID=0 (core)
        if (origID_list[c] == 0) and (clusterID_list[c] != 0):
            #print("Setting core position reference from clusterID={0} origID={1}".format(clusterID_list[c],origID_list[c]))
            core_xpos = cl_info.iloc[0].loc['avg_x']-cl_info.iloc[0].loc['core_x']
            core_ypos = cl_info.iloc[0].loc['avg_y']-cl_info.iloc[0].loc['core_y']
        # plot x vs. y center position
        ax.scatter(xpos,ypos,marker="x",s=50,color="k")
        # plot a FWHM ellipse
        majFWHM = cl_info.iloc[0].loc['fwhm_maj']/pix_to_mas      # major axis = FWHM
        minFWHM = cl_info.iloc[0].loc['fwhm_min']/pix_to_mas      # minor axis = FWHM
        cpa = cl_info.iloc[0].loc['cpa']
        if ptype == 'Color':
            ecolor = 'y'
        else:
            ecolor = 'g'    
        ax.add_patch(patches.Ellipse((xpos,ypos), minFWHM, majFWHM, color=ecolor,
                                     angle=cpa, linestyle='dotted',
                                      linewidth=1, fill=False, zorder=2))
    
    # Now plot clean component positions in a separate group offset from
    #  the contour image.  This time use actual model cluster properties
 
    # extract position data for each cluster model in this epoch 
    ref_epoch = cl_ep_df.iloc[0].loc['ref_epoch']
    cldataX = core_xpos+np.array(cl_ep_df['centX']+cl_ep_df['slopeX']*(epoch-ref_epoch)\
                       +0.5*cl_ep_df['accelX']*(epoch-ref_epoch)**2)
    cldataY = core_ypos+np.array(cl_ep_df['centY']+cl_ep_df['slopeY']*(epoch-ref_epoch)\
                       +0.5*cl_ep_df['accelY']*(epoch-ref_epoch)**2)
    # convert x, y mas data to pixels for plotting
    cldataX = np.array(image.header['CRPIX1'] - 1 + cldataX/pix_to_mas_x)
    cldataY = np.array(image.header['CRPIX2'] - 1 + cldataY/pix_to_mas_y)

    if (xshift is None) and (yshift is None):
        offset = 7.0*np.nanmedian(epoch_info_arr['bmaj'])/pix_to_mas
        if lims[0]-lims[1] < lims[3]-lims[2]:
            xshift = offset
            yshift = 0
        else:
            yshift = -offset
            xshift = 0          
    else:     # convert to pixels if one was specified
        if xshift is None:
            xshift = 0
        else:
            xshift /= pix_to_mas
        if yshift is None:
            yshift = 0
        else:
            yshift /= pix_to_mas
    
    # put cluster labels at half this offset 
    for i in range(0,len(cldataX)):
        ax.text(cldataX[i]+xshift/2,cldataY[i]+yshift/2,str(clusterID_list[i]),ha='center',va='center')
        if xshift == 0:
            ax.vlines(cldataX[i], cldataY[i]+0.8*yshift, cldataY[i]+0.6*yshift,
                     linestyles='dotted',colors='k',linewidth=1.0)
            ax.vlines(cldataX[i], cldataY[i]+0.4*yshift, cldataY[i]+0.2*yshift,
                     linestyles='dotted',colors='k',linewidth=1.0)
        else:
            ax.hlines(cldataY[i], cldataX[i]+0.8*xshift, cldataX[i]+0.6*xshift,
                     linestyles='dotted',colors='k',linewidth=1.0)
            ax.hlines(cldataY[i], cldataX[i]+0.4*xshift, cldataX[i]+0.2*xshift,
                     linestyles='dotted',colors='k',linewidth=1.0)
            

    # Plot clean components and cluster outlines
    #
    for i in range(-1,Nclusters):
        # select data to plot
        if i > -1:
            epoch_data = data[(data['epoch']==epoch)*(labels==origID_list[i])]
        else:
           epoch_data = data[(data['epoch']==epoch)*(labels==-1)]
         # convert x, y mas data to pixels for plotting
        xdata = np.array(image.header['CRPIX1'] - 1 + (epoch_data['x']-core_x)/pix_to_mas_x + xshift)
        ydata = np.array(image.header['CRPIX2'] - 1 + (epoch_data['y']-core_y)/pix_to_mas_y + yshift)
        # plot points
        if i > -1:
            ax.scatter(xdata,ydata,marker='.',color=cl_colors[clusterID_list[i]%(len(cl_colors))],alpha=0.6)
        else:
            ax.scatter(xdata,ydata,marker='.',color='k',alpha=0.75)
     
        # plot the cluster size in three rings of 1, 2, 3 sigma outlines
        #  --> NOTE: *not* FWHM 
        if i > -1: 
            cl_info = cl_ep_df[cl_ep_df['clusterID']==clusterID_list[i]]
            horz_width = 2*cl_info.iloc[0].loc['sizeMin']/pix_to_mas
            vert_width = 2*cl_info.iloc[0].loc['sizeMaj']/pix_to_mas
            pos_ang = cl_info.iloc[0].loc['sizePA']
            # Define cluster centers at current epoch
            cl_x = core_xpos+cl_info.iloc[0].loc['centX']+\
                   cl_info.iloc[0].loc['slopeX']*(epoch-ref_epoch)+\
                   0.5*cl_info.iloc[0].loc['accelX']*(epoch-ref_epoch)**2
            cl_y = core_ypos+cl_info.iloc[0].loc['centY']+\
                   cl_info.iloc[0].loc['slopeY']*(epoch-ref_epoch)+\
                   0.5*cl_info.iloc[0].loc['accelY']*(epoch-ref_epoch)**2
            cl_x = image.header['CRPIX1'] - 1 + cl_x/pix_to_mas_x + xshift
            cl_y = image.header['CRPIX2'] - 1 + cl_y/pix_to_mas_y + yshift

            # plot center point for cluster 
            ax.scatter(cl_x,cl_y,marker="+",s=100,color="k")
            # set the linestyle for the ellipses
            Lstyle = "--"
               
            ax.add_patch(patches.Ellipse((cl_x,cl_y), 
                                          horz_width, vert_width, angle=pos_ang, linestyle=Lstyle,
                                         linewidth=1, fill=False, zorder=2))
            ax.add_patch(patches.Ellipse((cl_x,cl_y), 
                                          2*horz_width, 2*vert_width, angle=pos_ang, linestyle=Lstyle, 
                                         linewidth=1, fill=False, zorder=2))
            ax.add_patch(patches.Ellipse((cl_x,cl_y), 
                                          3*horz_width, 3*vert_width, angle=pos_ang, linestyle=Lstyle, 
                                         linewidth=1, fill=False, zorder=2))

    #
    # revise plotting limits, based on shift
    #
    if np.any(np.array(lims) != 0):       
        # translate to pixel coord first
        x1 = image.header['CRPIX1'] - 1 + lims[0]/pix_to_mas_x 
        x2 = image.header['CRPIX1'] - 1 + lims[1]/pix_to_mas_x 
        y1 = image.header['CRPIX2'] - 1 + lims[2]/pix_to_mas_y
        y2 = image.header['CRPIX2'] - 1 + lims[3]/pix_to_mas_y
        if xshift > 0: 
            x2 += xshift
            #x1 -= xshift/2
        else:
            x1 += xshift
            #x2 -= xshift/2
        if yshift > 0:
            y2 += yshift
            #y1 -= yshift/2
        else:
            y1 += yshift
            #y2 -= yshift/2
        # set limits
        ax.set_xlim(x1,x2)
        ax.set_ylim(y1,y2)
    
    # provide a title for the plot
    if ptype == 'Color':
        ax.set_title("Epoch {0}".format(epoch))
    else:
        ax.set_title("Epoch {0}, cbase={1:0.2f} mJy/beam".format(epoch,1000*cbase))

    return fig, ax, cb

#----------------------------------------------------------------------------
#
# Function to loop over epochs and create a set of overlapping
#   'windows' (subsets) of winN epochs each... with each window
#   separated from the previous one by shifting winStep epochs
#
#  winN = number of epochs in each window
#  winStep = number of epochs to shift the start of next window (subset)
#  maxGap = maximum time Gap that can be spanned between two neighboring
#           epochs inside a 'window'... a larger timegap between epochs
#           will trigger a completely window of epochs
#  min_epoch = smallest epoch in decimal year to consider
#  max_epoch = largest epoch in decimal year to consider
#  epoch_info = array of epoch information
#  
#  returns a series of stop and end times for each window found
#
def create_epoch_windows(epoch_info, min_epoch, max_epoch, winN=9, 
                         windowing=True, maxGap=2.0, min_winN=5):
 
    # if windowing is disabled, just create one big window
    if not(windowing):
        print("Windowing disabled -- creating one big window")
        win_info = np.array([(0,
                              np.min(epoch_info['epoch_val']),
                              np.median(epoch_info['epoch_val']),
                              np.max(epoch_info['epoch_val']),
                              len(epoch_info),
                              0)],
                            dtype=window_datatype)
        return win_info 
    
    #
    # compute and find window time boundaries
    #
    win_info = np.array([], dtype = window_datatype)
    winID = 0

    # find gaps between epochs, to see if we can form a window
    gaps = np.array(epoch_info['epoch_val'][1:]) - np.array(epoch_info['epoch_val'][:-1])
    #
    # If the gaps are larger than maxGap, we will not be able to form a window
    print("------------------------------------------------------------------------------")
    print("Median gap between epochs: {0:.4f} years".format(np.median(gaps)))
    print("Gaps larger than {0:.4f} years will not be included in windows".format(maxGap))
    for i in range(len(gaps)):
        if gaps[i] > maxGap:
            print("Epoch {0:.4f} is followed by a gap of {1:.4f} years".format(epoch_info['epoch_val'][i], gaps[i]))
    #
    # Loop over epochs, treating each one as a potential median epoch of
    #  a window of epochs, and then looking for epochs around it
    for i in range(len(epoch_info['epoch_val'])):
        #  Get the median epoch value for this epoch
        #  and use it as the center of the window
        #
        ep = epoch_info['epoch_val'][i]
        #
        # Skip epochs that are outside the range we want to consider
        #
        if ep < min_epoch or ep > max_epoch:
            continue
        #
        # Epoch must have enough other epochs around it to form a window
        #.  without gaps larger than maxGap
        #
        diff_eps = epoch_info['epoch_val'] - ep
        #
        for winSize in np.arange(winN, min_winN-1, -2, dtype=int):
            N_side = np.floor(winSize/2).astype(int)
            if i < N_side or i + N_side >= len(epoch_info['epoch_val']):
                # Not enough epochs to form a window of this size
                continue
            # compute the gaps inside the window 
            #   (don't include last epoch in window -- guaranteed by indexing below)
            gaps_in_window = gaps[i-N_side:i+N_side]
            #
            if not(np.any(gaps_in_window > maxGap)):
                #
                # We have enough epochs without a gap to form a window around this median epoch
                #  with the specified size.
                #
                FirstEp = epoch_info[i-N_side]['epoch_val']
                LastEp = epoch_info[i+N_side]['epoch_val']
                if winSize < winN:
                    print("Warning: window size {0} is smaller than requested {1} "
                          "for median epoch {2:.4f}".format(winSize, winN, ep))
                win_info = np.append(win_info,
                                     np.array((winID, FirstEp, ep, LastEp, winSize, 0),
                                              dtype=window_datatype))
                winID += 1
                break  # break out of the winSize loop, we have found a window
    print("------------------------------------------------------------------------------")

    print("Looking for epochs not included in a previous window...".format(maxGap))
    for i in range(len(epoch_info['epoch_val'])):
        ep = epoch_info['epoch_val'][i]
        if ep < min_epoch or ep > max_epoch:
            continue
        if np.any( (win_info['first_epoch'] <= ep) * (win_info['last_epoch'] >= ep) ):
            # this epoch is already included in a previous window
            continue    
        else:
            print("Epoch {0:.4f} not included in previous windows, adding isolated epoch as its own window".format(ep))  
            #print("Adding isolated epoch as its own window")
            win_info = np.append(win_info,
                                    np.array((winID, ep, ep, ep, 1, 0),
                                            dtype=window_datatype))
            winID += 1
    print("------------------------------------------------------------------------------")

    # Sort windows by median epoch
    win_info = np.sort(win_info, order='median_epoch')
    # Set winID values again
    for i in range(len(win_info)):
        win_info[i]['winID'] = i

    # return window information array
    return win_info

#----------------------------------------------------------------------------
#
# A function to take the overlapping 'windows' or subsets of epochs
#  and iteratively run the clustering algorithm and store the results
# 
#  Check first to see if results already exist, and skip those cases
#
#  winMinEp = list of minimum epochs defining start of each window 
#  winMaxEp = list of maximum epochs defining end of each window
#  ccdata = clean component data
#  epoch_info = array of epoch information
#  min_clusters = min number of clusters to fit to each window
#  max_clusters = max number of clusters to fit to each window
#
# NOTE: in addition to the returned variables, win_info['Nclusters'] will be 
#.      populated with best estimates based on bic and bic* and smoothing
#
def run_epoch_window_fits(source, band, 
                          win_info, ccdata, epoch_info, min_clusters, max_clusters,
                          RefEpochType="Median",  # "Median" or "Middle" 
                          Fit_Accel=False,   # Don't fit accelerations in windowed fits????
                          StokesQU_weight=1e-9,
                          ClusterType="KMeans", CoreIDMethod="JetEnd",
                          JetDir=None, complex = 3.0,
                          EGauss=True, SigmaCut=0.0, StationaryCore=True,
                          overwrite_results = False,
                          ReCalculate=False,
                          print_diag=False, 
                          results_dir="",
                          input_core_pos=None,
                          save_name="test_win_save", Threads=1, run_history=None):
    #
    # Remove previous results from main Results directory as these come from
    #  older code versions
    #
    for extension in ['.csv','.npz','.merged_win_results.npz']:
        for filepath in glob.glob(save_name+".????.??-????.??"+extension):
            #print(filepath)
            try:
                os.remove(filepath)
                print(f"Removed: {filepath}")
            except OSError as e:
                print(f"Error removing {filepath}: {e}")

    #
    # If no sub-directory for cluster_fits exists, create one 
    # 
    if not os.path.exists(results_dir+"cluster_fits"):
        try:
            os.mkdir(results_dir+"cluster_fits")
            print("Created {0}".format(results_dir+"cluster_fits"))
        except:
            print("Error creating {0}".format(results_dir+"cluster_fits"))
            exit(0)

    # adjust save_name for this function to include the cluster_fits sub-directory
    save_fit_name = save_name.replace(results_dir,results_dir+"cluster_fits/")
           
    #
    # if requested, remove all previous results
    #                          
    if ReCalculate: 
        print("Removing all previous window fitting results...")
        for extension in ['.csv','.npz']:
            for filepath in glob.glob(save_fit_name+".????.??-????.??"+extension):
                try:
                    os.remove(filepath)
                    print(f"Removed: {filepath}")
                except OSError as e:
                    print(f"Error removing {filepath}: {e}")
    #
    # Create lists to store results of the fit to each window of epochs
    #
    data_win = []
    epochs_win = []
    clusters_win = []
    results_win = []
    results_df_win = []
    #
    # Some control variables for loops
    #
    save_file = []    # list of results filenames
    needs_compute = []  # list of windows that need to be comupted

    # function to check if ccdata is close for dtype=cc_datatype
    def cc_data_isclose(a,b):
        if a.shape != b.shape:
            return False
        if not np.allclose(a['epoch'], b['epoch']):
            return False
        if not np.allclose(a['x'], b['x']):
            return False
        if not np.allclose(a['y'], b['y']):
            return False
        if not np.allclose(a['flux'], b['flux']):
            return False
        return True

    #
    # Loop over windows and create all the filenames and
    #  check whether we need to compute this case
    #
    for win in win_info:
        i = win['winID']
        winMinEp = win['first_epoch']
        winMaxEp = win['last_epoch']   
        #
        # Create save name for this window of epochs
        #
        save_fit_name_win = save_fit_name + ".{0:.2f}-{1:.2f}".format(winMinEp,winMaxEp)
        save_file.append(save_fit_name_win)         
        #
        # See if that file already exists, and if so, skip creating it
        #
        if not(os.path.exists(save_file[i]+".npz")) or overwrite_results:
            print("{0} results need to be computed and saved".format(save_file[i]))
            needs_compute.append(i)
            run_history.append("# Calculated results for {0}.\n".format(save_file[i]))
        else:
            print("{0} results already exist, testing for changes".format(save_file[i]))
            npzfile = np.load(save_file[i]+".npz",allow_pickle=True)
            test_results_df1 = pd.read_csv(save_file[i]+".csv")
            #
            # define arrays from npzfile
            #
            test_data1 = npzfile['data']

            #
            # Check to see if any data or epochs have changed in this window
            #
            data1, temp, _ = select_epoch_range(ccdata, epoch_info, winMinEp, winMaxEp,show_info=False)
        
            if not cc_data_isclose(test_data1, data1):
                print("Dataset has changed for {0}, recomputing...".format(save_file[i]))
                needs_compute.append(i)
                run_history.append("# Recalculated results for {0} based on data changes.\n".format(save_file[i]))
            else:
                print("No changes detected for {0}, using existing results...".format(save_file[i]))    
    #
    # Create a driver function for parallel computing the cases that need it
    #
    def compute_window(n):
        #
        # Go ahead and fit a series of cluster models to this window of epochs
        #
        winMinEp = win_info[n]['first_epoch']
        winMaxEp = win_info[n]['last_epoch']    
        test_data1, test_epochs1, test_results1, test_clusters1, test_results_df1 =\
            test_cluster_num(source, band, ccdata,epoch_info,min_clusters, max_clusters,
                                winMinEp, winMaxEp, 
                                Fit_Accel=Fit_Accel,
                                RefEpochType=RefEpochType,
                                StokesQU_weight=StokesQU_weight,
                                #print_diag=True, print_info=True,
                                ClusterType=ClusterType, CoreIDMethod=CoreIDMethod,
                                JetDir=JetDir,
                                EGauss=EGauss, SigmaCut=SigmaCut, 
                                StationaryCore=StationaryCore,
                                input_core_pos=input_core_pos,
                                sfile=save_file[n]) 
        return 

    #
    # Run the parallel jobs...
    #
    Parallel(n_jobs=Threads)(delayed(compute_window)(n) for n in needs_compute)

    #
    # Once that is done, read all the results from disk and store them in the lists
    #
    for sfile in save_file:
        #
        # Load files
        #
        npzfile = np.load(sfile+".npz",allow_pickle=True)
        test_results_df1 = pd.read_csv(sfile+".csv")
        #
        # define arrays from npzfile
        #
        test_data1 = npzfile['data']
        test_epochs1 = npzfile['ep_info']
        test_results1 = npzfile['test_results']
        test_clusters1 = npzfile['clusters']
        #
        # Append results of fitting to lists created above.
        #
        data_win.append(test_data1)
        epochs_win.append(test_epochs1)
        results_win.append(test_results1)
        clusters_win.append(test_clusters1)
        results_df_win.append(test_results_df1)

    #
    # Setup 'Nclusters' estimates based on fits to each window
    #
    N_win = []
    #N_diff = []
    for i in range(len(win_info)):
        #
        # Create dataframe for this window
        #
        df = results_df_win[i]
        epoch_count = len(epochs_win[i])
        #
        mask=(df['ID']==0) 
        df = df[mask].copy()
        df=df.rename(columns={'Ncluster':'Nclusters'})
        #
        # Calculate the bic_* N_win estimates, adding in complexity factor, 
        #   and ensure it is within bounds
        #
        if 'k' in df.columns:
            k = df['k']
        else:
            k = (df['Nclusters']-int(StationaryCore))*4
            if Fit_Accel and epoch_count > 9:
                k = (df['Nclusters']-int(StationaryCore))*6   # degrees of freedom from cluster positions and slopes
            if StationaryCore:
                k += epoch_count*2  # for floating core positions 
        Ndata_est = df['Ndata_mean_inoise_cut']

        #  Calculate BIC* values
        df['bic*']= np.log(Ndata_est)*k+complex*Ndata_est*df["mean_dsqr"]/df['mean_sum_beam_sqr']
        # Determine N estimate from minimum bic*
        N_est = df.iloc[np.argmin(df['bic*'])].loc['Nclusters']
        if N_est < min_clusters:
            N_est = min_clusters
        elif N_est > max_clusters:
            N_est = max_clusters
        N_win.append(N_est)
        #
        if print_diag:
            #
            # Show a summary table for this window
            #
            columns = ["Nclusters","mean_dsqr","log_like","bic*",'frac_cc','frac_fl','overlap',
                       "Ndata_mean", "Ndata_mean_inoise_cut"]
            table=df[columns]
            pd.set_option('display.precision', 3)
            print(table)
            #
            print("N estimate for window (includes complex {0}) = {1}".format(complex, N_est))

    # Convert to numpy array and attache to win_info array
    N_win = np.array(N_win)
    #win_info['Nclusters'] = np.rint(scipy.signal.savgol_filter(N_win, window_length=5, polyorder=1, mode='nearest')).astype(int)
    win_info['Nclusters']= np.rint(N_win).astype(int)

    # print out the initial N_win values for each window
    print("Initial N_win values for each window based on BIC* values using --complex {0:.2f}:\n{1}".format(complex, N_win))

    run_history.append("# Estimated Nclusters for each window based on BIC* values using --complex {0:.2f}\n".format(complex))

    # 
    # return these lists of results
    #
    return data_win, epochs_win, results_win, clusters_win, results_df_win

#
# Function to get previous cluster labels from an existing results file
#
def get_previous_Nclusters_labels(prev_results_file, win_info, max_clusters, 
                                  cluster_results_win, Ncluster_array_win,
                                  ReCalculate_Nwin, Recalculate_crossIDs):
    # initialize cluster labels for tracking clusters across multiple windows
    labels_win = np.full((len(win_info),max_clusters), 1000)
    # initialize Nclusters values
    Nclusters_win = np.full(len(win_info),1000)   
    #
    prev_results_df = None

    # First check if we have an previous results file, and if so, import
    #.  Ncluster choices and cluster labels if we are not going to recalculate 
    if os.path.exists(prev_results_file):
        prev_results_df = pd.read_csv(prev_results_file)
        #
        if ReCalculate_Nwin:    # just return immediately because we have all we need
            return Nclusters_win, labels_win, prev_results_df
        #
        for i in win_info['winID']:
            prev_epoch_mask = prev_results_df['epoch'] == win_info[i]['median_epoch']
            core_mask = prev_results_df['clusterID']==0
            # should be only a single core match (at most)
            if np.sum(prev_epoch_mask*core_mask) == 1:
                Nclusters_win[i] = (prev_results_df[prev_epoch_mask*core_mask]['Nclusters']).iloc[0]
            elif np.sum(prev_epoch_mask) > 1:
                print("Multiple epoch match to previous results, check data")
                exit()
            else:
                continue  # no match found, leave Nclusters_win as 1000

            if not(Recalculate_crossIDs):
                #
                # setup labels for clusters in this window's median epoch
                #.  --> compare to current results to find matches
                #   --> exclude clusterID = -1 which represents unused components
                #
                result_dict = cluster_results_win[i]
                cluster_array = Ncluster_array_win[i]
                _, _ , _, cluster_epoch_df, _, _ =\
                    get_fit_results(result_dict[cluster_array==Nclusters_win[i]][0])
                #
                epoch_mask = cluster_epoch_df['epoch'] == win_info[i]['median_epoch']
                cluster_epoch_df = cluster_epoch_df[epoch_mask*(cluster_epoch_df['clusterID']>=0)]
                prev_results_epoch_df = prev_results_df[prev_epoch_mask*(prev_results_df['clusterID']>=0)]
                #
                # loop assumes same order of clusters in previous and current results
                #
                for j in range(0,len(prev_results_epoch_df)):
                    prev_cluster_label = prev_results_epoch_df['clusterID'].iloc[j]
                    prev_xpos = prev_results_epoch_df['avg_x'].iloc[j]
                    prev_ypos = prev_results_epoch_df['avg_y'].iloc[j]
                    curr_xpos = cluster_epoch_df['avg_x'].iloc[j]
                    curr_ypos = cluster_epoch_df['avg_y'].iloc[j]
                    #print("Comparing previous cluster {0} at ({1:.3f}, {2:.3f}) to current cluster at ({3:.3f}, {4:.3f})".format(
                    #    prev_cluster_label, prev_xpos, prev_ypos, curr_xpos, curr_ypos))    
                    if (np.isclose(prev_xpos, curr_xpos, rtol=0.01, atol=0.001) \
                        and np.isclose(prev_ypos, curr_ypos, rtol=0.01, atol=0.001)) \
                        or (np.isnan(prev_xpos) and np.isnan(curr_xpos)):
                        # we have a position match, assign the label
                        labels_win[i][j] = prev_cluster_label

    return Nclusters_win, labels_win, prev_results_df

#----------------------------------------------------------------------------
#
# Function to compare two clusters from different fits to see if they
#  might be a match.  Returns a comparision metric that if < 1 indicates
#  a match.  
#
# The current algorithm uses brightness temp. and position to make
#  this comparison.  New: flux and area differences also have to match
#  within the same Tb_log_diff scaling factor.
#
#  oID, tID => id labels of clusters in the two fits
#  ocldata_df, tcldata_df => dataframes with the relevant information about
#                            each fit
#  Tb_log_diff, size_pos_fact => Scaling factors to determine the degree of
#                                agreement
#
def compare_metric(oID, tID, ocldata_df, tcldata_df, 
                   median_bmaj,median_bmin, 
                   flux_log_diff=0.15, size_pos_fact=0.1, area_log_diff=0.3,
                   print_diag=False):
    
    # compute agreement metrics from cluster_data only
    omask = (ocldata_df['origID']==oID)
    tmask = (tcldata_df['origID']==tID)
    if np.sum(omask) != 1 or np.sum(tmask) != 1:
        #print("Not exactly one cluster ID match!")
        return 100.0
    
    # get values for comparison:
    oflux = ocldata_df[omask]['iflux'].iloc[0]
    tflux = tcldata_df[tmask]['iflux'].iloc[0]

    # Return 0 or undefined flux cases immediately
    if oflux == 0 or np.isnan(oflux) or tflux == 0 or np.isnan(tflux):
        return 100.0
 
    # get remaining values for comparison
    ocentX = ocldata_df[omask]['avg_x'].iloc[0]-ocldata_df[omask]['core_x'].iloc[0]
    ocentY = ocldata_df[omask]['avg_y'].iloc[0]-ocldata_df[omask]['core_y'].iloc[0]
    tcentX = tcldata_df[tmask]['avg_x'].iloc[0]-tcldata_df[tmask]['core_x'].iloc[0]
    tcentY = tcldata_df[tmask]['avg_y'].iloc[0]-tcldata_df[tmask]['core_y'].iloc[0]
    omaj = ocldata_df[omask]['fwhm_maj'].iloc[0]
    omin = ocldata_df[omask]['fwhm_min'].iloc[0]
    opa = ocldata_df[omask]['cpa'].iloc[0]*np.pi/180.00
    tmaj = tcldata_df[tmask]['fwhm_maj'].iloc[0]
    tmin = tcldata_df[tmask]['fwhm_min'].iloc[0]
    tpa = tcldata_df[tmask]['cpa'].iloc[0]*np.pi/180.00

    oTb = oflux/(omaj*omin)
    tTb = tflux/(tmaj*tmin) 

    oepoch = ocldata_df[omask]['epoch'].iloc[0]
    tepoch = tcldata_df[tmask]['epoch'].iloc[0] 

       
    #
    # compute the difference relative to the scale we set
    # 
    #Tb_diff = np.abs(np.log10(tTb)-np.log10(oTb))/Tb_log_diff
    #if Tb_diff > 1.0 and not (print_diag):   # return easy cases immediately
    #    return 100.0
    
    flux_diff = np.abs(np.log10(tflux)-np.log10(oflux))/flux_log_diff
    if flux_diff > 1.0 and not (print_diag):   # return easy cases immediately
        return 100.0    
    area_diff = np.abs(np.log10(tmaj*tmin)-np.log10(omaj*omin))/area_log_diff
    if area_diff > 1.0 and not (print_diag):   # return easy cases immediately
        return 100.0

    #
    # Compute positional difference comparison in this epoch
    #

    # Calculate the dist and position angle from cluster "o" to cluster "t" and vice-versa
    dist = np.sqrt((tcentX-ocentX)**2+(tcentY-ocentY)**2)
    phi_ot = np.arctan2((tcentX-ocentX),(tcentY-ocentY)) # in radians!
    phi_to = np.arctan2((ocentX-tcentX),(ocentY-tcentY)) # in radians! 

    # Calculate the size, s, of cluster "o" in the direction of cluster "t" and vice-versa
    #   See 2013-05-22 handwritten notes for details      
    size_ot =  omaj*omin/np.sqrt((omaj*np.sin(phi_ot-opa))**2+(omin*np.cos(phi_ot-opa))**2)
    size_to =  tmaj*tmin/np.sqrt((tmaj*np.sin(phi_to-tpa))**2+(tmin*np.cos(phi_to-tpa))**2)

    #
    # Compute scaled distance between clusters 
    #   --> make maximum allowed difference = the FHWM median beam in that direction
    #   --> make minimum allowed difference = 1/10th the FWHM median beam in that direction
    #
    median_beam_fwhm_in_dir = median_bmaj*median_bmin/np.sqrt((median_bmaj*np.sin(phi_ot))**2+(median_bmin*np.cos(phi_ot))**2)
    divisor = np.min([(size_ot+size_to)*size_pos_fact,median_beam_fwhm_in_dir])
    divisor = np.max([divisor,median_beam_fwhm_in_dir/10])
    position_diff = dist/divisor

    if print_diag:
        print("Comparing oID={0} at epoch {1} to tID={2} at epoch {3}".format(oID,oepoch,tID,tepoch))
        print("  oTb={0:0.2e}, tTb={1:0.2e}, Tb_diff={2:0.2f}".format(oTb,tTb,Tb_diff))
        print("  oPos={0:0.2f}, {1:0.2f}, tPos={2:0.2f}, {3:0.2f}".format(ocentX,ocentY,tcentX,tcentY))
        print("  oSize={0:0.2f},{1:0.2f} at {2:0.1f} deg, tSize={3:0.2f},{4:0.2f} at {5:0.1f} deg".format(omaj,omin,opa*180/np.pi,
                                                                                                          tmaj,tmin,tpa*180/np.pi))
        print(size_ot, size_to)
        print(position_diff)


    if np.abs(position_diff) > 1.0 or np.abs(flux_diff) > 1.0 or np.abs(area_diff) > 1.0:
        return 100.0
    else:
        return np.sqrt(flux_diff**2+position_diff**2+area_diff**2)/3
 
#----------------------------------------------------------------------------
#
# Function to compare two cluster fits, shifted in time and find an 
#  agreement metric for all the clusters in the "orig" fit compared
#  one-by-one to the "test" fit.  
#
#  The number of clusters in the two fits may differ from one another
#    
def compare_cluster_fits(orig_clusters, orig_results, Norig, test_clusters, test_results, Ntest,
                         median_bmaj, median_bmin, 
                         flux_log_diff=0.15, size_pos_fact=0.1, area_log_diff=0.3,
                         print_diag=False):
    #
    # extract information from the orig cluster fits for Norig clusters
    #
    _,oref_epoch,_,ocluster_epoch_df,_,_ = get_fit_results(orig_results[orig_clusters==Norig][0])

    #
    # extract information from the orig cluster fits for Ntest clusters
    #
    _,tref_epoch,_,tcluster_epoch_df,_,_ =get_fit_results(test_results[test_clusters==Ntest][0])

    #
    # compute agreement metrics from cluster_data by comparing the two time periods
    #  --> this computes a full matrix of all 'orig' clusters vs. all 'test' clusters
    #
    cmetric = np.full([Norig,Ntest],100.0)
    for oID in range(1,Norig):
        for tID in range(1,Ntest):
            # Compare at both epochs to be fair
            ocldata_df = ocluster_epoch_df[ocluster_epoch_df['epoch']==oref_epoch]
            tcldata_df = tcluster_epoch_df[tcluster_epoch_df['epoch']==oref_epoch]
            cmetric1 = compare_metric(oID,tID,ocldata_df,tcldata_df,
                                    median_bmaj,median_bmin,
                                    flux_log_diff,size_pos_fact,area_log_diff,
                                    print_diag)
            if cmetric1 < 100.0:
                ocldata_df = ocluster_epoch_df[ocluster_epoch_df['epoch']==tref_epoch]
                tcldata_df = tcluster_epoch_df[tcluster_epoch_df['epoch']==tref_epoch]
                cmetric2 = compare_metric(oID,tID,ocldata_df,tcldata_df,
                                        median_bmaj,median_bmin, 
                                        flux_log_diff,size_pos_fact,area_log_diff,
                                        print_diag) 
                #
                # average the two metrics, otherwise they will default to 100 as initialized above
                cmetric[oID,tID] = 0.5*(cmetric1+cmetric2)
            #
            if print_diag:
                print(oID, tID, cmetric1, cmetric2, cmetric[oID,tID])
    #
    # Consider agreements from old->test and test->old
    #   ** require a metric <= 1.0 for 'agreement'
    #
    agreements_w_orig = np.sum(cmetric <= 1.0,axis=1)
    agreements_w_test = np.sum(cmetric <= 1.0,axis=0)
    tweights = np.ones(len(agreements_w_test)) 
    if print_diag:
        print("Weights of test data:\n ", tweights)

    #
    # any sums != 1 indicate disagreement or multiple-agreement (just as bad!)
    #
    if print_diag and (np.any(agreements_w_orig > 1) or np.any(agreements_w_test > 1)):
        print("We have multiple matching clusters")
        print(np.round(cmetric, 2))
    #
    # set any multiple matches as if there was no agreement 
    #
    agreements_w_orig[agreements_w_orig != 1] = 0
    agreements_w_test[agreements_w_test != 1] = 0 

    #
    # calculate a mean agreement to give an overall sense of how good a match
    #  these two models are, but return much more information.
    #
    if np.sum(agreements_w_test) > 0:
        agree_scores = np.min(cmetric,axis=0)*agreements_w_test 
        median_agree_metric = np.median(agree_scores[agreements_w_test > 0])
    else:
        median_agree_metric = 100
    
    if print_diag:
        print("Agreement matrix (Norig={0}, Ntest={1}):".format(Norig,Ntest))
        print(np.round(cmetric, 2))
        print("Agreements w orig: ", agreements_w_orig)
        print("Agreements w test: ", agreements_w_test)
        print("Sum of agreements w test: ", np.sum(agreements_w_test))
        print("Median agreement metric: ", median_agree_metric)

    return cmetric, agreements_w_test, np.sum(agreements_w_test), median_agree_metric


#----------------------------------------------------------------------------
#
# Function to start at the beginning of a series of 'windows' or subsets of
#  epochs and perform two functions to the cluster fits to those windows
#
#  (1) If requested, use the estimated number of clusters in each window, N_win,
#      and allow direct user visual editing to those values.  
#
#  (2)  Once the number of clusters for each time window is determined, final cluster
#       matching is performed across all windows in time sequence without changing N in
#       those windows.  The goal here is to match clusters across time, so that
#       the matched clusters are given the same labels and the non-matching clusters
#       are given new labels (although those might match with something further down
#       the line in future comparisons)
#
#  ccdata = array of ccdata for all epochs
#  epoch_info = array of epoch information for all epochs
#  root_data_dir = root directory for data storage
#  minN = minimum number of clusters to consider for any window
#  maxN = maximum number of clusters to consider for any window
#  win_info = array of information about each window of time
#  edit_N_win = boolean to indicate if we want to interactively edit N_win
#  prev_Nclusters_win = previous Nclusters values for each window to start from
#  prev_labels_win = previous cluster labels for each window to start from
#  Ncluster_arrays = reference arrays that let us find the fit results for a given
#                   number of clusters in a given clustering window
#  cluster_results = results saved for clustering window and range of Nclusters fit
#                    to that window.
#  Tb_log_diff = scaling factor for brightness temperature difference
#  size_pos_fact = scaling factor for size and position difference
#
def cluster_window_matching(ccdata, epoch_info, root_data_dir,
                            minN, maxN, win_info,
                            Ncluster_arrays, cluster_results,
                            edit_N_win = False,
                            prev_Nclusters_win=None,
                            prev_labels_win=None, 
                            flux_log_diff=0.15, size_pos_fact=0.1, area_log_diff=0.3,
                            print_diag=False, run_history=None):
    
    # calculate number of windows and extract start/end epochs
    num_Wins = len(win_info)
    winStarts = win_info['first_epoch']
    winEnds = win_info['last_epoch']    

    # use an array of Nclusters in each window to prevent us from changing the original!
    N_win = win_info['Nclusters'].copy()

    # get the median beam
    median_bmaj = np.nanmedian(epoch_info['bmaj'])
    median_bmin = np.nanmedian(epoch_info['bmin'])

    # set N_win values from previous results if provided
    if np.sum(prev_Nclusters_win != 1000) > 0:
        print("Previous Ncluster values found:{0}".format(prev_Nclusters_win))
        # Formatted printout of previous Ncluster values for each window
        run_history.append("# Ncluster values from previous run:\n")
        history_string = "#   ["
        for i in range(num_Wins):
            history_string += "{0:3d}".format(prev_Nclusters_win[i])
            if i < num_Wins-1:
                history_string += ", "
            else:
                history_string += " ]\n"
                run_history.append(history_string)
            if (i+1)%10 == 0:
                history_string += "\n"
                run_history.append(history_string)
                history_string = "#    "
        # Override N_win values with previous results where they are reasonable, and print out the changes
        for i in range(num_Wins):
            if prev_Nclusters_win[i] != N_win[i]:
                if prev_Nclusters_win[i] >= minN and prev_Nclusters_win[i] <= maxN:
                    print("Overriding N_win for window {0} from {1} to {2} based on previous results".format(
                            i, N_win[i], prev_Nclusters_win[i]))
                    N_win[i] = prev_Nclusters_win[i]
                else:
                    run_history.append("#   Overriding previous Ncluster value in window {0} from {1:.2f} to {2:.2f} to be {3}.\n".format(i, winStarts[i], winEnds[i], N_win[i]))
    else:
        run_history.append("# No previous Ncluster values provided, using bic* calculated values:\n")
        history_string = "#   ["
        for i in range(num_Wins):
            history_string += "{0:3d}".format(N_win[i])
            if i < num_Wins-1:
                history_string += ", "
            else:
                history_string += " ]\n"
                run_history.append(history_string)
            if (i+1)%10 == 0:
                history_string += "\n"
                run_history.append(history_string)
                history_string = "#    "    


    #
    #   See if we want to interactively edit N_win
    #
    if edit_N_win:
        N_win1 = N_win_edit(ccdata, epoch_info, root_data_dir,
                         N_win, num_Wins,  winStarts, winEnds,
                         Ncluster_arrays, cluster_results)
        if np.any(N_win1 != N_win):
            print("Original N_win:\n{0}".format(N_win))
            print("Edited N_win:\n{0}".format(N_win1))
            keep = input("Keep edited values? [Y]/n: ")
            if keep in ['','Y','y','yes','Yes']:
                print("Using updated values for N_win")
                run_history.append("# Updated N_win values from interactive editing:\n")
                history_string = "#   ["
                for i in range(num_Wins):
                    history_string += "{0:3d}".format(N_win1[i])
                    if i < num_Wins-1:
                        history_string += ", "
                    else:
                        history_string += " ]\n"
                        run_history.append(history_string)
                    if (i+1)%10 == 0:
                        history_string += "\n"
                        run_history.append(history_string)
                        history_string = "#    "
                # invalidate previous labels where N_win changed
                for i in range(num_Wins):
                    if N_win1[i] != N_win[i]:
                        if prev_labels_win is not None:
                            prev_labels_win[i,:] = 1000
                            run_history.append("# Invalidated previous cross-IDs for window {0} from {1:.2f} to {2:.2f}.\n".format(i, winStarts[i], winEnds[i]))
                N_win = N_win1.copy()
            else:
                print("Keeping original N_win values")
    #
    # Loop over windows of epochs, now with best N values already determined above,  
    #   to update the labels based on agreements of clusters across time
    #
    print("Assigning Labels based on N_win values found...")
    #
    # define a max unused label for re-labelling non-matching clusters
    #
    max_unused_label = 1  # start at 1 because we will always give the core label 0
    #
    # setup arrays to carry label information
    #   for each of the windowed fits, substitute previous labelling if we have it
    #
    if prev_labels_win is not None and np.any(prev_labels_win != 1000):
        labels_win = prev_labels_win.copy()
        max_unused_label = np.max([max_unused_label, np.max(labels_win[labels_win != 1000])+1]) 
        ID_updates_needed = False
        for i in range(num_Wins):
            if np.sum(labels_win[i] != 1000) != N_win[i]:
                ID_updates_needed = True
                break
        if ID_updates_needed: 
            print("Starting max_unused_label = {0} from previous labels".format(max_unused_label))
            run_history.append("# Cross-IDs only automatically updated where needed. Starting max_unused_label = {0} from previous labels\n".format(max_unused_label))
            run_history.append("#  --> Cross-ID updates may not be possible or may be inconsistent in the modified epochs, please check manually.\n")
        else:
            print("All cross-IDs for all windows already assigned, no updates needed...")
            run_history.append("# All cross-IDs for all windows already assigned, no updates needed...\n")
            return N_win, labels_win
    else:        
        labels_win = np.full((num_Wins,maxN), 1000)
        run_history.append("# No previous cross-IDs provided, all Cross-IDs made automatically\n".format(max_unused_label))

    #   
    # label clusters in the first window
    #.  --> work in rerverse order to give smallest labels to earliest clusters
    #

    # First look to see if the second epoch has labels, if so, try to match to those and use the same label if we get an agreement
    if len(N_win) > 1 and np.sum(labels_win[1] != 1000) > 0:
        metric_array, agreements, _, _=\
        compare_cluster_fits(Ncluster_arrays[1],cluster_results[1],N_win[1],
                            Ncluster_arrays[0],cluster_results[0],N_win[0],
                            median_bmaj, median_bmin,
                            flux_log_diff, size_pos_fact, area_log_diff,
                            print_diag)
    else:
        agreements = np.zeros(N_win[0])

    # Now label the first window, working in reverse order to give smallest labels to earliest clusters, 
    #    and using agreement with second window where possible
    if np.sum(labels_win[0] != 1000) == N_win[0]:
        print("All cross-IDs for Window {0} at {1} already assigned, skipping...".format(0, win_info['median_epoch'][0]))
    else:
        print("Assigning cross-IDs for Window {0} at {2}, Nclusters = {1}".format(0, N_win[0], win_info['median_epoch'][0]))   
        for j in reversed(range(0,N_win[0])):
            if labels_win[0,j] == 1000:
                if j == 0:
                    labels_win[0,0] = 0  # core always gets label 0
                elif agreements[j] == 1:
                    if print_diag:
                        print("-- Assigning cross-ID {0} to cluster {1} in window 0 based on agreement with window 1".format(labels_win[1][np.argmin(metric_array,axis=0)[j]], j))
                    labels_win[0,j] = labels_win[1][np.argmin(metric_array,axis=0)[j]]
                else:
                    if print_diag:
                        print("-- Assigning new cross-ID {0} to cluster {1} in window 0 based on no agreement with window 1".format(max_unused_label, j))
                    labels_win[0,j] = max_unused_label
                    max_unused_label += 1 

    #
    # Do all the remaining windows...
    #
    for i in range(0,num_Wins-1):
        #
        if np.sum(labels_win[i+1] != 1000) == N_win[i+1]:
            print("All cross-IDs for Window {0} at {1} already assigned, skipping...".format(i+1, win_info['median_epoch'][i+1]))
            continue  # already assigned from previous results
        #
        print("Assigning cross-IDs for Window {0} at {2}, Nclusters = {1}".format(i+1, N_win[i+1], win_info['median_epoch'][i+1]))
        metric_array, agreements, _, _=\
        compare_cluster_fits(Ncluster_arrays[i],cluster_results[i],N_win[i],
                            Ncluster_arrays[i+1],cluster_results[i+1],N_win[i+1],
                            median_bmaj, median_bmin,
                            flux_log_diff, size_pos_fact, area_log_diff,
                            print_diag)
        #
        # update labels to carry matching labels forward and give new labels
        #   where needed
        #. --> work in reverse order to give smallest labels to earliest clusters
        #
        for j in reversed(range(0,N_win[i+1])):
            if labels_win[i+1][j] != 1000:
                continue  # already assigned from previous results
            elif j == 0:
                labels_win[i+1][0] = 0 
            elif agreements[j] == 1:
                if print_diag:
                    print("-- Assigning cross-ID {0} to cluster {1} in window {2} based on agreement".format(labels_win[i][np.argmin(metric_array,axis=0)[j]], j, i+1))
                labels_win[i+1][j] = labels_win[i][np.argmin(metric_array,axis=0)[j]]
            else:
                # 
                # First check one epoch further back to see if we can get an agreement for this feature
                #. --> but require improved agreement by a factor of two
                #
                if i > 0:
                    metric_array2, agreements2, _, _ =\
                    compare_cluster_fits(Ncluster_arrays[i-1],cluster_results[i-1],N_win[i-1],
                                        Ncluster_arrays[i+1],cluster_results[i+1],N_win[i+1],
                                        median_bmaj, median_bmin,
                                        flux_log_diff/2, size_pos_fact/2, area_log_diff/2,
                                        print_diag=False)
                else:
                    agreements2 = np.zeros(N_win[i+1])
                #
                if agreements2[j] == 1:
                    if print_diag:
                        print("-- Assigning cross-ID from window {0} to cluster {1} in window {2} based on 2-window agreement".format(i-1, j, i+1))
                    labels_win[i+1][j] = labels_win[i-1][np.argmin(metric_array2,axis=0)[j]]
                else: 
                    if print_diag:
                        print("-- Assigning new cross-ID {0} to cluster {1} in window {2}".format(max_unused_label, j, i+1))                       
                    labels_win[i+1][j] = max_unused_label
                    max_unused_label += 1

   
    return N_win, labels_win

#------------------------------------------------------ 
# Function to interactively view and edit N_win selections 
#------------------------------------------------------ 
# This function allows the user to view the N cluster
#. estimates for each window, try different values,
#. and record those changes.
#
def N_win_edit(ccdata, epoch_info, root_data_dir,
                N_win_vals, num_Wins,  winStarts, winEnds,
                Ncluster_arrays, cluster_results):
    
    # prevent changing original!
    N_win = N_win_vals.copy()

    # Create a list of epochs in data and 
    #   a list of indicies that point to the reference_epochs for each window
    epoch_list = epoch_info['epoch_val']
    ref_epoch_indicies = np.full(num_Wins, 0, dtype=int)
    for i in range(num_Wins):
        ref_epoch_indicies[i] = np.argwhere(epoch_list == ((cluster_results[i])[Ncluster_arrays[i]==1][0])['ref_epoch'])[0][0]
    #    
    print(epoch_list[ref_epoch_indicies])  # should print all the reference epochs, assuming they correspond
                                           #.  to real epochs... if not, something went wrong in fitting

    #
    # define an internal function to get results 
    #   from window "win", with "N" clusters
    #
    #.  --> returns both cluster results and the index 
    #       of the reference epoch for this window
    #
    def get_window_fit_results(win, N): 
        #
        results_dict = cluster_results[win] 
        cluster_array = Ncluster_arrays[win]
        #
        # Extract results from results dictionary
        #    
        _, ref_epoch, _, cluster_epoch_df, labels, _ =\
            get_fit_results(results_dict[cluster_array==N][0])
        #
        # retrieve integer index for the reference epoch of this window
        epoch_int = np.argwhere(epoch_list == ref_epoch)[0][0]
        #
        win_data, _, _ = select_epoch_range(ccdata,epoch_info,winStarts[win],winEnds[win],show_info=False)

        return cluster_epoch_df,labels,epoch_int,win_data,cluster_array

    #
    # Start with results from the most complex window
    #
    win = np.argmax(N_win)
    cluster_epoch_df,labels,epoch_int,win_data,cluster_array=get_window_fit_results(win,N_win[win])

    #
    # use these complex window results to estimate plotting limits...
    #

    # get max, min positions relative to the core for defining plotting area
    #  --> include cluster size estimates 
    xpos = cluster_epoch_df['avg_x']-cluster_epoch_df['core_x']
    ypos = cluster_epoch_df['avg_y']-cluster_epoch_df['core_y']
    #xpos = cluster_epoch_df['centX']
    #ypos = cluster_epoch_df['centY']
    median_beam = np.nanmedian(epoch_info['bmaj'])
    xmin = np.min(xpos - 2*cluster_epoch_df['sizeMaj']) - 1.5*median_beam
    xmax = np.max(xpos + 2*cluster_epoch_df['sizeMaj']) + 1.5*median_beam
    ymin = np.min(ypos - 2*cluster_epoch_df['sizeMaj']) - 1.5*median_beam
    ymax = np.max(ypos + 2*cluster_epoch_df['sizeMaj']) + 1.5*median_beam
    xspan = xmax-xmin
    yspan = ymax-ymin
    xrange = [ xmin - 0.05*xspan , xmax + 0.05*xspan ]
    yrange = [ ymin - 0.05*yspan , ymax + 0.05*yspan ]
    #
    lims=[xrange[1],xrange[0],yrange[0],yrange[1]]

    # compute shifts for images and clean components
    if xspan < yspan:
        xshift = 0.7*xspan
        yshift = 0
    else:
        yshift = -0.7*yspan
        xshift = 0          

    # Start with the first window and corresponding epoch
    cluster_epoch_df,labels,epoch_int,win_data,cluster_array=get_window_fit_results(0,N_win[0])
    
    #
    # show an image with clusters superimposed
    #
    fig, ax, _ = overplot_clusters(epoch_info, win_data, root_data_dir,
                                cluster_epoch_df, labels, epoch_int,
                                xshift = xshift, yshift = yshift,
                                lims=lims)
    fig.show()
    fig.canvas.draw_idle()

    ax_info = fig.add_axes([0.02,0.97,0.9,0.02])
    ax_info.text(0,0,"Use arrow keys or sliders to change epochs and clusters. Exit window when done.")  
    ax_info.set_axis_off()

    #
    # Create room for reference epoch and cluster sliders
    #
    fig.subplots_adjust(bottom=0.25)
        
    #
    # Add an epoch slider
    #
    ax_epochs = fig.add_axes([0.15, 0.01, 0.65, 0.03])
    
    # only include valid reference epochs in the slider
    sepochs = Slider(
        ax_epochs, "Reference Epoch", 
        epoch_list[ref_epoch_indicies[0]], 
        epoch_list[ref_epoch_indicies[-1]],
        valinit=epoch_list[ref_epoch_indicies[0]], 
        valstep=np.array(epoch_list[ref_epoch_indicies]),
        color="green"
    )
    sepochs.vline._linewidth = 0.  # remove the initial value line

    def update_epoch(val):
        # update cluster data
        win = np.argwhere(epoch_list[ref_epoch_indicies]==sepochs.val)[0][0]
        cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
        #
        sclusters.valinit = N_win[win]
        sclusters.reset()
        overplot_clusters(epoch_info, win_data, root_data_dir,
                                cl_ep_df, cl_labels, epoch_int,
                                xshift = xshift, yshift = yshift,
                                lims=lims,fig=fig,ax=ax)
        #
        fig.canvas.draw_idle()
        
    sepochs.on_changed(update_epoch)

    #
    # Add a cluster slider
    #
    ax_clusters = fig.add_axes([0.15, 0.05, 0.65, 0.03])
    
    sclusters = Slider(
        ax_clusters, "Clusters", cluster_array[0], cluster_array[-1],
        valinit=N_win[0], valstep=cluster_array,
        color="blue"
    )
    sclusters.vline._linewidth = 0 # remove the initial value line

    def update_clusters(val):
        win = np.argwhere(epoch_list[ref_epoch_indicies]==sepochs.val)[0][0]
        N_win[win] = int(sclusters.val)
        # get new values for new cluster fit
        cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
        #
        overplot_clusters(epoch_info, win_data, root_data_dir,
                                cl_ep_df, cl_labels, epoch_int,
                                xshift = xshift, yshift = yshift,
                                lims=lims,fig=fig,ax=ax)
        #
        fig.canvas.draw_idle()

        #return win, N_win, cl_ep_df, cl_labels

    sclusters.on_changed(update_clusters)   

    #
    # Allow and manage some keypress events
    # 
    def on_press(event):
        win = np.argwhere(epoch_list[ref_epoch_indicies]==sepochs.val)[0][0]
        #
        if (event.key == 'n' or event.key == 'right') and sepochs.val < sepochs.valmax:
            win = win+1
            # get new values for new cluster fit
            cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
            #
            sepochs.valinit = epoch_list[ref_epoch_indicies[win]]
            sepochs.reset()
            sclusters.valinit = N_win[win]
            sclusters.reset()
            overplot_clusters(epoch_info, win_data, root_data_dir,
                        cl_ep_df, cl_labels, epoch_int,
                            xshift = xshift, yshift = yshift,
                        lims=lims,fig=fig,ax=ax)      
            fig.canvas.draw_idle()
        if (event.key == 'b' or event.key == 'left') and sepochs.val > sepochs.valmin:
            win = win-1
            # get new values for new cluster fit
            cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
            #
            sepochs.valinit = epoch_list[ref_epoch_indicies[win]]
            sepochs.reset()
            sclusters.valinit = N_win[win]
            sclusters.reset()
            overplot_clusters(epoch_info, win_data, root_data_dir,
                        cl_ep_df, cl_labels, epoch_int,
                            xshift = xshift, yshift = yshift,
                        lims=lims,fig=fig,ax=ax)      
            fig.canvas.draw_idle()
        if (event.key == 'up') and sclusters.val < sclusters.valmax:
            N_win[win] += 1
            # get new values for new cluster fit
            cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
            #
            sclusters.valinit = N_win[win]
            sclusters.reset()
            overplot_clusters(epoch_info, win_data, root_data_dir,
                        cl_ep_df, cl_labels, epoch_int,
                            xshift = xshift, yshift = yshift,
                        lims=lims,fig=fig,ax=ax)      
            fig.canvas.draw_idle()
        if (event.key == 'down') and sclusters.val > sclusters.valmin:
            N_win[win] -= 1
            # get new values for new cluster fit
            cl_ep_df,cl_labels,epoch_int,win_data,_=get_window_fit_results(win,N_win[win])
            #
            sclusters.valinit = N_win[win]
            sclusters.reset()
            overplot_clusters(epoch_info, win_data, root_data_dir,
                        cl_ep_df, cl_labels, epoch_int,
                            xshift = xshift, yshift = yshift,
                        lims=lims,fig=fig,ax=ax)      
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect('key_press_event', on_press)
        
    #input("Hit Enter when done.")
    plt.show()  # will block program from continuing until window is closed

    return N_win  

#----------------------------------------------------------------------------
#
# Function to update cluster IDs
#
def update_clusterIDs(cl_epoch_df, event_key, run_history=None):
    #
    if np.sum(cl_epoch_df['select']) == 0:
        print("No clusters selected yet, cannot update properties")
        return 
    #
    print("----------------------------------------------------------------------")

    if event_key == 'r':
        print("Toggling robustness for *all* epochs of selected clusterIDs")
        selected_mask = (cl_epoch_df['select'] == True)
        uniqueIDs = np.unique(cl_epoch_df[selected_mask]['clusterID'])
        for cID in uniqueIDs:
            ID_mask = (cl_epoch_df['clusterID']==cID)
            current_robust = cl_epoch_df[ID_mask]['robust'].iloc[0]
            for index,row in cl_epoch_df[ID_mask].iterrows():
                cl_epoch_df.at[index,'robust'] = not(current_robust)
            print("Set cluster {0} as robust={1}".format(cID, not(current_robust)))
            run_history.append("# Set cluster {0} as robust={1}\n".format(cID, not(current_robust)))
        cl_epoch_df['select'] = False


    if event_key == 'u':
        print("Toggling use_in_fit on selected clusters")
        selected_mask = (cl_epoch_df['select'] == True)
        for index,row in cl_epoch_df[selected_mask].iterrows():
            cl_epoch_df.at[index,'use_in_fit'] = not(row['use_in_fit'])
            run_history.append("# Set cluster {0} in epoch {1} use_in_fit={2}\n".format(row['clusterID'], row['epoch'], not(row['use_in_fit'])))
            cl_epoch_df.at[index,'select'] = False

    if event_key in ['i','a']:
        #
        # Get the new ID from the user.
        #
        if event_key == 'i':
            new_ID=input("Re-ID all selected clusters as (integer): ")
        else:
            if np.sum(cl_epoch_df['select']) != 1:
                print("Only a single cluster can be re-ID'd at a time with the 'a' key, please select only one cluster and try again")
                return
            else:
                id_to_change = cl_epoch_df[cl_epoch_df['select']]['clusterID'].iloc[0]
                print(f"Re-IDing all selected clusters with current ID={id_to_change}")
                id_mask = (cl_epoch_df['clusterID'] == id_to_change)
                cl_epoch_df.loc[id_mask,'select'] = True
            new_ID=input("Re-ID all instances of the selected cluster as (integer): ")
            
        try:
            new_ID = int(new_ID) 
        except:
            print("Please enter a valid integer between 0 and 999")
            return
        #
        # One more check on value
        #
        if not(new_ID >=0 and new_ID < 1000):
            print("{new_ID} is out of range, must be integer from 0 to 999")
            return

        #
        # if new_ID indicates a core
        #
        overlap_flag=False
        overlap_flag=False
        for index, row in cl_epoch_df.iterrows():
            if row['select']:
                # create a mask for this epoch
                epoch_mask = (cl_epoch_df['epoch'] == row['epoch'])
                # Check if there are any other clusters at this epoch with the
                #   same label... if so, set them to 999
                for index2, row2 in cl_epoch_df[epoch_mask].iterrows():
                    if row2['clusterID'] == new_ID:
                        if new_ID != 999:
                            print(f"Warning, other comps in {row['epoch']} had ID={new_ID}")
                            print(" --> Those will be set to ID=999")
                            overlap_flag=True
                        if cl_epoch_df.at[index2,'clusterID'] == 0:
                            print(f"Warning, a comp 0 in {row['epoch']} reassigned to 999!")
                            print(" --> this can be OK if another comp has ID=0")
                        cl_epoch_df.at[index2, 'clusterID'] = 999
                        run_history.append("# Re-ID cluster {0} in epoch {1} from {2} to 999 due to overlap with new ID {3}\n".format(
                            row2['clusterID'], row2['epoch'], row2['clusterID'], new_ID))
                    if row2['select'] and index != index2:
                        if new_ID != 999:
                            print(f"Warning, multiple comps in {row['epoch']} were selected")
                            print(" --> Only the first will be given the newID, the others")
                            print("     will be set to ID=999")
                # set the current one 
                if cl_epoch_df.at[index,'clusterID'] == 0 and new_ID != 0:
                    print(f"Warning, a comp 0 in {row['epoch']} reassigned to {new_ID}!")
                    print(" --> this can be OK if another comp has ID=0")
                cl_epoch_df.at[index, 'clusterID'] = new_ID
                cl_epoch_df.at[index, 'select'] = False
                run_history.append("# Re-ID cluster {0} in epoch {1} to {2}\n".format(
                    row['clusterID'], row['epoch'], new_ID))
                # If we have a new core cluster, adjust positions accordingly
                if new_ID == 0:
                    # Assign core positions in all rows at this epoch
                    for index2, row2 in cl_epoch_df[epoch_mask].iterrows():
                        cl_epoch_df.at[index2,'core_x'] = row['avg_x']
                        cl_epoch_df.at[index2,'core_y'] = row['avg_y']
                                
        if new_ID == 0:
            print("Changing all selected clusters to a coreID = 0")
            print(" --> positions relative to the core in each epoch were recomputed")
            run_history.append("# Changed all selected clusters to a coreID = 0\n")
            run_history.append("#   --> Recomputed positions relative to the core in each epoch\n")
        else:
            print(f"Selected comps set to ID = {new_ID}")

        if overlap_flag:
            print("Overlapping IDs in the same epochs are set to ID=999")
 
    if np.sum(cl_epoch_df['select']) == 0:
        print("All selected comps are now de-selected")
    else:
        print("Warning, some comps still selected!")
    print("----------------------------------------------------------------------")


#----------------------------------------------------------------------------
#
# A function to grab epoch name from epoch_info
#  array if we know the epoch decimal value
#
def get_epoch_name(decimal_epoch, epoch_info):
    ep_mask = (epoch_info['epoch_val'] == decimal_epoch)
    if np.sum(ep_mask) == 1:         # a single match!
        return epoch_info[ep_mask]['epoch_name'][0]
    else:
        return ''

#----------------------------------------------------------------------------
#
# A function to construct a dataframe from the individual windowed fits
#   Dataframe = cluster properties calculated from clean components 
#               in each epoch using the model from the windowed
#               fit closest in time to that epoch.
#   Cluster labels in the dataframe are taken from the 
#   windowed fit selection and pairing (which value of N clusters
#   to use from one windowed fit the next and which fitted clusters
#   can be cross-ID'd)
#
#   win_info = Information about each window of epochs fit
#   N_Win = Array of Number of clusters to use for each window
#   labels_win = Array of labels to use for clusterIDs for each window
#   epoch_info = Array of epoch information
#   ccdata_win = Array of clean component data used for the clustering fits to each window
#   Ncluster_arrays = reference arrays that let us find the fit results for a given
#                   number of clusters in a given clustering window
#   cluster_results = results saved for clustering window and range of Nclusters fit
#                    to that window.
#
def construct_cluster_epoch_df(win_info, N_win, labels_win, epoch_info, ccdata_win, 
                               Ncluster_arrays, cluster_results, relabel_ccdata=True,
                               prev_cl_df = None, run_history=None):

    #
    # Create arrays to store the cluster_epoch_df information
    #   and the clean component data information
    
    cl_epoch_df = []
    cc_data = np.array([],dtype=cc_datatype)

    #
    # For each epoch, collect the right information from the correct 
    #   clustering fit to the nearest window of epochs in time.
    #
    for ep in epoch_info:
        #
        # Find the windowed fit with a median_epoch closest in time that has data from this epoch
        #
        win_mask = (win_info['first_epoch'] <= ep['epoch_val']) & (win_info['last_epoch'] >= ep['epoch_val'])
        if np.sum(win_mask) == 0:
            print("No windowed fit contains epoch {0}, skipping...".format(ep['epoch_val']))
            continue    
        closest_in_winmask = np.argmin(np.abs(ep['epoch_val']-win_info[win_mask]['median_epoch']))
        closest_win = (win_info[win_mask])[closest_in_winmask]['winID']
        #
        # Retrieve the cluster results for that epoch
        #
        results_index = np.argwhere(Ncluster_arrays[closest_win] == int(N_win[closest_win]))[0,0]
        cl_results = cluster_results[closest_win][results_index]
        #
        # Bulid up a cluster_epoch dataframe by combining best matching epochs
        #   from each of the windowed fit
        #
        cluster_epoch_df1 = cl_results['cluster_epoch_df']
        cluster_mask = (cluster_epoch_df1['epoch']==ep['epoch_val'])
        #        
        cluster_epoch_df = cluster_epoch_df1[cluster_mask].copy()
        cluster_epoch_df['ep_name'] = get_epoch_name(ep['epoch_val'], epoch_info)
        #
        # Assign new cluster labels in this epoch based on windowed fit assignments found above
        #
        for index, row in cluster_epoch_df.iterrows():
            if row.loc['clusterID'] < 0:   # leave unassigned flux alone and don't relabel
                continue
            if row.loc['clusterID'] != 0 and labels_win[closest_win][row.loc['clusterID']] == 0:
                print("Warning, re-assigning a non-core cluster to core ID=0 in epoch {0}!".format(row.loc['epoch']))
                print(" --> adjusting core position to this cluster's average position")
                # update core positions for this epoch
                for index2, row2 in cluster_epoch_df.iterrows():
                    if row2.loc['epoch'] == row.loc['epoch']:
                        cluster_epoch_df.loc[index2,'core_x'] = cluster_epoch_df.loc[index,'avg_x']
                        cluster_epoch_df.loc[index2,'core_y'] = cluster_epoch_df.loc[index,'avg_y']
            # final assignment of new cluster ID
            cluster_epoch_df.loc[index,'clusterID'] = labels_win[closest_win][row.loc['clusterID']]

        #
        # Assign beam and noise information to cluster_epoch_df
        cluster_epoch_df['bmaj'] = ep['bmaj']
        cluster_epoch_df['bmin'] = ep['bmin']
        cluster_epoch_df['bpa']  = ep['bpa']
        cluster_epoch_df['inoise'] = ep['inoise']
        cluster_epoch_df['pnoise'] = ep['pnoise']
        # Append to global list
        cl_epoch_df.append(cluster_epoch_df)
        #
        # Similar to above, build up a global data list with the correct labels
        #    for each epoch
        #
        ep_data1 = ccdata_win[closest_win].copy()
        ep_data1['clusterID'] = cl_results['labels']
        ep_data = ep_data1[ep_data1['epoch']==ep['epoch_val']].copy()
        #
        # Assign new cluster labels in this epoch based on windowed fit assignments found above
        #
        if relabel_ccdata:
            for i in range(len(ep_data)):
                if ep_data[i]['clusterID'] < 0:   # leave unassigned cc alone and don't relabel
                    continue
                ep_data[i]['clusterID'] = labels_win[closest_win][ep_data[i]['clusterID']]
        #   
        cc_data = np.append(cc_data, ep_data)
        #print(cc_data[-1])
    
    
    cl_df = pd.concat(cl_epoch_df, ignore_index=True)
    cc_labels = cc_data['clusterID']

    #
    # Assign use_in_fit based on number of Stokes I components + match to predicted location by cluster model
    # 
    USE_LIM = 10.0
    cl_df['use_in_fit'] = (cl_df['iflux'] >= USE_LIM*cl_df['inoise'])&(cl_df['clusterID'] >= 0)
    # poor matches to the position of the cluster model in that epoch are not used in fit
    dist_to_cluster_model = np.sqrt((cl_df['avg_x'] - cl_df['pred_x'])**2 + (cl_df['avg_y'] - cl_df['pred_y'])**2)
    cl_df['use_in_fit'] &= (dist_to_cluster_model <= 2.0*cl_df['sizeMaj']) 
    #
    # Assign robustness based on number of useable epochs
    #
    ROBUST_LIM = 5
    cl_df['robust'] = False
    for i in np.unique(cl_df['clusterID']):
        label_mask = (cl_df['clusterID']==i)
        use_in_fit = np.array(cl_df['use_in_fit'][label_mask]).copy()
        #
        # Set those that meet the threshold as robust
        #
        if np.sum(use_in_fit) >= ROBUST_LIM:
           robust_array = np.array(cl_df['robust']).copy()
           robust_array[label_mask] = True
           cl_df['robust'] = robust_array
             
    # Use previous cl_df results to keep any 'robust' and 'use_in_fit' changes
    if prev_cl_df is not None and 'robust' in prev_cl_df.columns and 'use_in_fit' in prev_cl_df.columns:
        # find cases where previous use_in_fits that don't match defaults
        if 'inoise' in prev_cl_df.columns:   # make sure we have noise info
            use_in_fit_default = (prev_cl_df['iflux'] >= USE_LIM*prev_cl_df['inoise'])&(prev_cl_df['clusterID'] >= 0)
            dist_to_cluster_model = np.sqrt((prev_cl_df['avg_x'] - prev_cl_df['pred_x'])**2 + (prev_cl_df['avg_y'] - prev_cl_df['pred_y'])**2)
            use_in_fit_default &= (dist_to_cluster_model <= 2.0*prev_cl_df['sizeMaj']) 
        else:  # accomodate older data without noise info, where we used 5 cc as the limit
            use_in_fit_default = (prev_cl_df['N_Icc'] >= 5)&(prev_cl_df['clusterID'] >= 0)
        mismatch_mask = (use_in_fit_default != prev_cl_df['use_in_fit'])
        if np.sum(mismatch_mask) > 0:
            run_history.append("# Updated use_in_fit values to match previous results for {0} cases where default use_in_fit values don't match previous values\n".format(np.sum(mismatch_mask)))
        for index,row in prev_cl_df[mismatch_mask].iterrows():
            cl_mask = (cl_df['clusterID']==row['clusterID'])
            cl_mask &= (cl_df['epoch']==row['epoch'])
            if np.sum(cl_mask) > 0:
                use_in_fit_array = np.array(cl_df['use_in_fit']).copy()
                use_in_fit_array[cl_mask] = row['use_in_fit']
                cl_df['use_in_fit'] = use_in_fit_array

        # find cases where previous robust values don't match defaults
        update_count = 0
        for i in np.unique(prev_cl_df['clusterID']):
            plabel_mask = (prev_cl_df['clusterID']==i) 
            use_in_fit = np.array(prev_cl_df['use_in_fit'][plabel_mask]).copy()
            if np.sum(use_in_fit) >= ROBUST_LIM:
                if prev_cl_df[plabel_mask]['robust'].iloc[0] == False:
                    label_mask = (cl_df['clusterID']==i)
                    robust_array = np.array(cl_df['robust']).copy()
                    robust_array[label_mask] = False
                    cl_df['robust'] = robust_array
                    update_count += 1
            elif prev_cl_df[plabel_mask]['robust'].iloc[0] == True:
                label_mask = (cl_df['clusterID']==i)
                robust_array = np.array(cl_df['robust']).copy()
                robust_array[label_mask] = True
                cl_df['robust'] = robust_array
                update_count += 1

        if update_count > 0:
            run_history.append("# Updated robust values to match previous results for {0} cases where default robust values don't match previous values\n".format(update_count))    
                        
    return cl_df, cc_data, cc_labels

#
# Function to plot a contour image of a given epoch with ccdata overlayed
#
def epoch_plot(epoch_info_arr, data, root_data_dir, epoch_int, 
                   core_pos,
                   xshift=None, yshift=None, offset=None,
                   lims=None, cbase=None, cstep=2.0,
                   ptype = 'Contour', 
                   cmap=cm.viridis, linthresh = 1e-3, vmax=0, vmin=-1e-99,
                   fig=None, ax=None, cb=None, axes='Affine'):
    #
    if lims is None:
        lims = [ 0, 0, 0, 0 ]
        
    # select specific values for this particular epoch
    epoch_info = epoch_info_arr[epoch_int]
    #print(epoch_info)
    epoch = epoch_info['epoch_val']

    # location for core_pos marker
    core_pos_loc = core_pos[epoch_int]
    
    # Get noise and base contours on that...
    if cbase is None:
        cbase = 3.5*epoch_info['inoise']

    # Load image
    image = fits.open(root_data_dir+epoch_info['fits_file'])[0]
    # pixels to mas conversion
    pix_to_mas_x = image.header['CDELT1']*(60*60*1000)
    pix_to_mas_y = image.header['CDELT2']*(60*60*1000)
    pix_to_mas = np.abs(pix_to_mas_x)
    #
    # Create image and return axes if we are not plotting on an existing figure
    #
    fig, ax, im = plot_image(image, lims=lims, iptype = ptype, 
                         inoise=epoch_info['inoise'],
                         ibase_factor=cbase/epoch_info['inoise'], cstep=cstep,
                         cmap=cmap, linthresh=linthresh, show_colorbar=False, 
                         vmax=vmax, vmin=vmin, remove_text=True,
                         axes = axes, fig=fig, ax=ax)
    if ptype == 'Color':
        #print(image.data[60:70,60:70])
        if cb is None: 
            cb = fig.colorbar(im, pad=0.01)
            cb.set_label(label="Stokes I Intensity",size='x-small')
            cb.ax.tick_params(labelsize='x-small')
        else:
            cb.update_normal(im)    

    #
    # Plot clean components
    #
    epoch_data = data[(data['epoch']==epoch)]
    xdata = np.array(image.header['CRPIX1'] - 1 + epoch_data['x']/pix_to_mas_x)
    ydata = np.array(image.header['CRPIX2'] - 1 + epoch_data['y']/pix_to_mas_y)
    ax.scatter(xdata,ydata,marker='.',color='gray',alpha=0.5)

    #
    # Plot core position marker
    # 
    cx = np.array(image.header['CRPIX1'] - 1 + core_pos_loc['x']/pix_to_mas_x)
    cy = np.array(image.header['CRPIX2'] - 1 + core_pos_loc['y']/pix_to_mas_y)
    ax.scatter(cx, cy, marker = 'x', color = 'b', alpha = 0.75, s=100)
    # provide a title for the plot
    if ptype == 'Color':
        ax.set_title("Epoch {0}".format(epoch))
    else:
        ax.set_title("Epoch {0}, cbase={1:0.2f} mJy/beam".format(epoch,1000*cbase))

    return fig, ax, cb, image.header['CRPIX1'], image.header['CRPIX2'], pix_to_mas_x, pix_to_mas_y


#---------------------------------------------------------------- 
# Function to interactively select core locations in each epoch
#---------------------------------------------------------------- 
#
def Interactive_Core_Selection(ccdata, epoch_info, root_data_dir):
    
    #
    # Create a list of epochs in data  
    #
    epoch_list = epoch_info['epoch_val']
    core_pos_list = np.zeros(len(epoch_list), dtype=corepos_datatype)

    # get max, min positions for defining plotting area
    #  
    xpos = ccdata['x']
    ypos = ccdata['y']
    #
    median_beam = np.nanmedian(epoch_info['bmaj'])
    xmin = np.min(xpos) - 1.5*median_beam
    xmax = np.max(xpos) + 1.5*median_beam
    ymin = np.min(ypos) - 1.5*median_beam
    ymax = np.max(ypos) + 1.5*median_beam
    xspan = xmax-xmin
    yspan = ymax-ymin
    xrange = [ xmin - 0.05*xspan , xmax + 0.05*xspan ]
    yrange = [ ymin - 0.05*yspan , ymax + 0.05*yspan ]
    #
    lims=[xrange[1],xrange[0],yrange[0],yrange[1]]

    #
    # show an image of the first epoch
    #
    epoch_int = 0
    fig, ax, _, cent_pix_x, cent_pix_y, pix_to_mas_x, pix_to_mas_y =\
                epoch_plot(epoch_info, ccdata, root_data_dir, epoch_int,
                            core_pos_list, lims=lims)

    fig.show()
    fig.canvas.draw_idle()

    ax_info = fig.add_axes([0.02,0.97,0.9,0.02])
    ax_info.text(0,0,"Use arrow keys or sliders to change epochs. Exit window when done.")  
    ax_info.set_axis_off()

    #
    # Create room for reference epoch and cluster sliders
    #
    fig.subplots_adjust(bottom=0.25)
        
    #
    # Add an epoch slider
    #
    ax_epochs = fig.add_axes([0.15, 0.01, 0.65, 0.03])
    
    # only include valid reference epochs in the slider
    sepochs = Slider(
        ax_epochs, "Epoch", 
        epoch_list[0], 
        epoch_list[-1],
        valinit=epoch_list[0], 
        valstep=np.array(epoch_list),
        color="green"
    )
    sepochs.vline._linewidth = 0.  # remove the initial value line

    def update_epoch(val):
        # update cluster data
        epoch_int = np.argwhere(epoch_list==sepochs.val)[0][0]
        epoch_plot(epoch_info, ccdata, root_data_dir, epoch_int,
                        core_pos_list, lims=lims,fig=fig,ax=ax)
        #
        fig.canvas.draw_idle()
        
    sepochs.on_changed(update_epoch)

    #
    # Allow and manage some keypress events
    # 
    def on_press(event):
        #
        if (event.key == 'n' or event.key == 'right') and sepochs.val < sepochs.valmax:
            epoch_int = np.argwhere(epoch_list==sepochs.val)[0][0]
            epoch_int = epoch_int+1
            sepochs.val = epoch_list[epoch_int]
            #sepochs.reset()
            epoch_plot(epoch_info, ccdata, root_data_dir, epoch_int,
                            core_pos_list, lims=lims,fig=fig,ax=ax)
            #
            fig.canvas.draw_idle()
        if (event.key == 'b' or event.key == 'left') and sepochs.val > sepochs.valmin:
            epoch_int = np.argwhere(epoch_list==sepochs.val)[0][0]
            epoch_int = epoch_int-1
            sepochs.val = epoch_list[epoch_int]
            #sepochs.reset()
            epoch_plot(epoch_info, ccdata, root_data_dir, epoch_int,
                            core_pos_list, lims=lims,fig=fig,ax=ax)
            #
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect('key_press_event', on_press)

    #
    # Allow and manage mouse clicks to identify core location
    #
    def onclick(event):
        core_x = pix_to_mas_x*(event.xdata - cent_pix_x + 1)
        core_y = pix_to_mas_y*(event.ydata - cent_pix_y + 1)
        epoch_int = np.argwhere(epoch_list==sepochs.val)[0][0]
        core_pos_list[epoch_int]['x'] = core_x
        core_pos_list[epoch_int]['y'] = core_y
        epoch_plot(epoch_info, ccdata, root_data_dir, epoch_int,
                        core_pos_list, lims=lims,fig=fig,ax=ax)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect('button_press_event', onclick)

    #input("Hit Enter when done.")
    plt.show()  # will block program from continuing until window is closed

    return core_pos_list  

#----------------------------------------------------------------------------
#
# A function to update mySQL for these cluster results
#
#  source = source name in B1950 coordinates (for MOJAVE)
#  userid = Three letter ID to use to insert model into mySQL
#  ctable = Name of component table to use, "components" for uband
#  cluster_epoch_df = Dataframe of cluster epoch properties, calculated from 
#                  clean components
#
def update_mySQL(source, userid, ctable, cluster_epoch_df, confirm=True):
    #
    # Define Server
    #
    server_name = "mojavedb.mpifr-bonn.mpg.de"
    
    #
    # Get the password for the database securely
    #
    mysqlpass = os.getenv('MYSQLPASS')
    if mysqlpass is None:
        print("Enter the mySQL password")
        mysqlpass = getpass.getpass()
    
    #
    # Open the connection
    #
    engine_url = f"mysql+pymysql://agn:{mysqlpass}@{server_name}/galaxies"
    mySQL_engine = create_engine(engine_url)
    #mySQL_connect = mySQL_engine.connect()
    try:
        mySQL_connect = mySQL_engine.connect()
        print("mySQL connection works!")
    except:
        print("mySQL connection not working!")
        return None
    

    #
    # Do a mySQL query to see what we have for this source, userid in components table
    #
    sql_query1 = "select epoch from {0} where source='{1}' and observer='{2}' and method='IM'".format(ctable,source,userid)
    sql_query1_df = pd.read_sql(sql_query1,mySQL_connect)
    unique_epochs = np.unique(sql_query1_df['epoch'].astype(str))
    #
    # If there are previous entries, decide what to do...
    #
    if len(unique_epochs) > 0:
        print("At least some epochs are already in the {0} table for {1} by {2} with method='IM'".format(ctable,source,userid))
        print(unique_epochs)
        if confirm:
            resp1 = input(" --> Should we replace matching epochs with new results? (y/n): ")
        else:
            resp1 = 'y'
        if resp1 in {'y','Y','yes','Yes'}:
            #
            # Check if we should just delete all previous entries and start fresh
            #
            if confirm:
                resp2 = input(" --> Should we delete non-matching epochs as well? (y/n): ")
            else:
                resp2 = 'y'
            if resp2 in {'y','Y','yes','Yes'}:
                sql_command = "delete from {0} where source='{1}' and observer='{2}' and method='IM'".format(ctable,source,userid)
                print("SQL: {0}".format(sql_command))
                mySQL_connect.execute(text(sql_command))
                mySQL_connect.commit()
            else:
                print("Leaving non-matching epochs with previous results, deleting all matching cases...")
                for ep_name in unqiue_epochs:
                    sql_command = "delete from {0} where source='{1}' and observer='{2}' and epoch='{3}' and method='IM'".format(ctable,source,userid,epoch)
                    print("SQL: {0}".format(sql_command))
                    mySQL_connect.execute(text(sql_command))
                    mySQL_connect.commit()                   
        else:
            print("Making no changes to mySQL")
            return None
    #
    # Convert 'robust' and 'use_in_fit' to a form that can be uploaded to mySQL
    #
    cluster_epoch_df['robust'] = np.int32(cluster_epoch_df['robust'])
    cluster_epoch_df['use_in_fit'] = np.int32(cluster_epoch_df['use_in_fit'])

    #
    # mySQL cannot handle np.nan, instead use 'NULL'
    #
    cluster_epoch_df['dist'] = np.sqrt(cluster_epoch_df['avg_x']**2+cluster_epoch_df['avg_y']**2)
    cluster_epoch_df['pa'] = np.arctan2(cluster_epoch_df['avg_x'],cluster_epoch_df['avg_y'])*180.0/np.pi
    cluster_epoch_df['ratio'] = cluster_epoch_df['fwhm_min']/cluster_epoch_df['fwhm_maj']
    cluster_epoch_df = cluster_epoch_df.replace({np.nan: 'NULL'})

    #
    # Now loop over new modelfitting results and add those to the table
    #
    for index, row in cluster_epoch_df.iterrows():
        # Skip clusterIDs that simply represent unassigned flux
        if row['clusterID'] < 0 or row['iflux'] == "NULL":
            continue
        #
        sql_command = "insert into {0} (source,epoch,observer,stokes,method,id,flux,dist,pa,size,ratio,cpa,use_in_fit,rating,qflux,uflux) ".format(ctable)
        sql_command += "values('{0}','{1}','{2}','{3}','{4}',{5},".format(source,row['ep_name'],userid,'i','IM',row['clusterID'])
        if row['qflux'] == 'NULL' or row['uflux'] == 'NULL':
            sql_command += "{0:.4e},{1:.4e},{2:.4e},{3:.4e},{4:.4e},{5:.4e},'{6}','{7}',NULL,NULL)".format(row['iflux'],
                                                                                         row['dist'], row['pa'],
                                                                                         row['fwhm_maj'],row['ratio'],row['cpa'], 
                                                                                         row['use_in_fit'], row['robust'])
        else:    
            sql_command += "{0:.4e},{1:.4e},{2:.4e},{3:.4e},{4:.4e},{5:.4e},'{6}','{7}',{8:.4e},{9:.4e})".format(row['iflux'],
                                                                                         row['dist'], row['pa'],
                                                                                         row['fwhm_maj'],row['ratio'],row['cpa'],
                                                                                         row['use_in_fit'], row['robust'],
                                                                                         row['qflux'], row['uflux'])
                                                                                                
        print("SQL: {0}".format(sql_command))
        mySQL_connect.execute(text(sql_command))
        mySQL_connect.commit() 

    

