import os, tqdm, glob, sys
import shutil
import numpy as np
from obspy import UTCDateTime as UTC
import pandas as pd
import logging
import logging.config
import signal
import time
import yaml

def setup_logging(
    default_path='rfsks_support/logging.yaml',
    default_level=logging.INFO,
    env_key='LOG_CFG',
    dirname='results/'
):
    """Setup logging configuration

    """
    path = default_path
    value = os.getenv(env_key, None)
    
    if value:
        path = value
    if os.path.exists(path):
        with open(path, 'rt') as f:
            config = yaml.safe_load(f.read())
        config['handlers']['info_file_handler']['filename'] = dirname+'tmp/info.log'
        config['handlers']['error_file_handler']['filename'] = dirname+'tmp/errors.log'
        logging.config.dictConfig(config)
        
    else:
        print("Selecting basic config")
        logging.basicConfig(level=default_level)

def rem_duplicate_lines(inpfile,outfile):
    lines_seen = set() # holds lines already seen
    outfile = open(outfile, "w")
    for line in open(inpfile, "r"):
        if line not in lines_seen: # not a duplicate
            outfile.write(line)
            lines_seen.add(line)
    outfile.close()

def create_dir(direc):
    '''
    Create a directory
    '''
    logger = logging.getLogger(__name__)
    try:
        os.makedirs(direc, exist_ok=True)
    except OSError:
        logger.info("--> Creation of the directory {} failed".format(direc))
    else:
        logger.info("--> Successfully created the directory {}".format(direc))

def rem_dir(direc):
    '''
    Delete a directory
    '''
    if os.path.exists(direc):
        shutil.rmtree(direc)

def read_directories(res_dir):
    with open('Settings/directories_names.yaml') as f:
        direc_dict = yaml.load(f, Loader=yaml.FullLoader)
    dirs = pd.DataFrame({'DIR_VAR':list(direc_dict.keys()),'DIR_NAME':list(direc_dict.values())})
    dirs.set_index('DIR_VAR',inplace=True)

    dirs['DIR_NAME'] = np.array([res_dir+val for val in dirs['DIR_NAME'].values])
    newdirname=[]
    for direc in dirs['DIR_NAME']:
        if direc[-1]!="/":
            newdirname.append(f"{direc}/")
        else:
            newdirname.append(f"{direc}")
    dirs['DIR_NAME']= np.array(newdirname)

    ## Separating RF and SKS directories
    rfdirs,sksdirs,otherdirs=[],[],[]
    for indx in dirs.index.values:
        if indx[:2]=='RF':
            rfdirs.append(dirs.loc[indx,'DIR_NAME'])
        elif indx[:3]=='SKS':
            sksdirs.append(dirs.loc[indx,'DIR_NAME'])
        else:
            otherdirs.append(dirs.loc[indx,'DIR_NAME'])
    return dirs,rfdirs,sksdirs,otherdirs


avg = lambda num1,num2: (int(num1)+int(num2))/2.0


def date2time(sta_sdate,sta_edate):
    logger = logging.getLogger(__name__)
    smonth = f'0{sta_sdate.month}' if sta_sdate.month < 10 else f'{sta_sdate.month}'
    emonth = f'0{sta_edate.month}' if sta_edate.month < 10 else f'{sta_edate.month}'
    sday = f'0{sta_sdate.day}' if sta_sdate.day < 10 else f'{sta_sdate.day}'
    eday = f'0{sta_edate.day}' if sta_edate.day < 10 else f'{sta_edate.day}'
    stime = f'{sta_sdate.year}-{smonth}-{sday}'
    etime = f'{sta_edate.year}-{emonth}-{eday}'

    return UTC(stime), UTC(etime)

def write_station_file(inventorytxtfile,rf_staNetNames,outfile):
    df_stations = pd.read_csv(inventorytxtfile,sep="|")
    allnetsrf = [netsta.split("_")[0] for netsta in set(rf_staNetNames)]
    allstnsrf = [netsta.split("_")[1] for netsta in set(rf_staNetNames)]
    df_stations_new = pd.DataFrame()
    for net, stn in zip(allnetsrf,allstnsrf):
        df_stations_extract = df_stations[(df_stations['#Network']==net) & (df_stations['Station']==stn)]
        df_stations_new = df_stations_new.append(df_stations_extract, ignore_index=True)
    df_stations_new.to_csv(outfile, index = None, header=True, sep = "|")


def obtain_inventory_events(rf_data,invRFfile,catalogxmlloc,network,station,dirs,minmagnitudeRF,maxmagnitudeRF,obtain_inventory=True,obtain_events=True):
    logger = logging.getLogger(__name__)
    if obtain_inventory:
        if not os.path.exists(invRFfile):
            try:
                logger.info("Trying to operate the get_stnxml method")
                logger.info("## Operating get_stnxml method")
                rf_data.get_stnxml(network=network, station=station)
            except Exception as e:
                logger.error(e)
                logger.error("Timeout while requesting...Please try again after some time", exc_info=True)
                # sys.exit()
    if obtain_events:
        logger.info("\n")
        logger.info("Obtaining events catalog")
        rf_data.obtain_events(catalogxmlloc=catalogxmlloc,catalogtxtloc=catalogxmlloc,minmagnitude=minmagnitudeRF,maxmagnitude=maxmagnitudeRF)


def concat_event_catalog(catfile,all_catalogtxt):
    logger = logging.getLogger(__name__)
    if len(all_catalogtxt)>1:
        logger.debug(f"length(all_catalogtxt): {len(all_catalogtxt)}")
        f = open(catfile, "w")
        for fl in all_catalogtxt:
            flr = open(fl,'r')
            f.write(flr.read())
            flr.close()
        f.close()
            
    elif len(all_catalogtxt)==1:
        shutil.copyfile(all_catalogtxt[0],catfile)
        if os.path.exists(catfile):
            logger.debug(f"Catalog file: {catfile}")


def select_to_download_events(catalogloc,datafileloc,dest_map,RFsta,rf_data,minmagnitudeRF,maxmagnitudeRF,plot_stations,plot_events,locations,method='RF'):
    logger = logging.getLogger(__name__)

    all_stations_df = pd.read_csv(RFsta, sep="|")
    nets = all_stations_df['#Network'].values
    stns = all_stations_df['Station'].values
    

    net_sta_list=[]
    for net, sta in zip(nets,stns):
        catfile = catalogloc+f"{net}-{sta}-events-info-{method}.txt"
        net_sta = f"{net}-{sta}"
        
        all_catalogtxt = glob.glob(catalogloc+f'{net}-{sta}-*-events-info-{method}.txt')
        # logger.info(len(all_catalogtxt),all_catalogtxt[0])
        if len(all_catalogtxt)!=0:
            concat_event_catalog(catfile,all_catalogtxt)
            # logger.info(f"Catalog file exists for {net_sta}!")
            if net_sta not in net_sta_list:
                net_sta_list.append(net_sta)
        else:
            logger.error(f"No catalog file exists for {net_sta}!", exc_info=True)


    if len(net_sta_list)==0:
        logger.error("No catalog file found! Exiting...")
        sys.exit()
        
    total_events,rem_events=0,0
    for net_sta in net_sta_list:
        net = net_sta.split("-")[0]
        sta = net_sta.split("-")[1]
        if not os.path.exists(datafileloc+f'{net}-{sta}-rf_profile_data.h5'):
            catfile = catalogloc+f"{net}-{sta}-events-info-{method}.txt"
            catfileout = catalogloc+f"{net}-{sta}-events-info-{method}-out.txt"
            rem_duplicate_lines(catfile,catfileout)
            shutil.move(catfileout,catfile)
            rem_events += int(pd.read_csv(catfile,sep="|",header=None).shape[0])
        total_events += int(pd.read_csv(catfile,sep="|",header=None).shape[0])
      

    if rem_events:
        logger.info("\n")
        logger.info("## Operating download method")
        rf_data.download_data(catalogtxtloc=catalogloc,datafileloc=datafileloc,tot_evnt_stns=total_events,rem_evnts=rem_events, plot_stations=plot_stations, plot_events=plot_events,dest_map=dest_map,locations=locations)
    else:
        logger.warning("No events found!")



 
class Timeout():
    """Timeout class using ALARM signal."""
    class Timeout(Exception):
        pass
 
    def __init__(self, sec):
        self.sec = sec
 
    def __enter__(self):
        signal.signal(signal.SIGALRM, self.raise_timeout)
        signal.alarm(self.sec)
 
    def __exit__(self, *args):
        signal.alarm(0)    # disable alarm
 
    def raise_timeout(self, *args):
        logger = logging.getLogger(__name__)
        try:
            raise Timeout.Timeout()
        except:
            pass


def organize_inventory(inventorytxtfile):
    '''
    create inventory text file containing information of stations with it's start and end time
    '''
    out_inventorytxtfile = inventorytxtfile.split(".")[0]+'_combined'+'.txt'
    inv_df = pd.read_csv(inventorytxtfile,sep="|",keep_default_na=False, na_values=[""])
 
    inv_df['EndTime'].fillna('2599-12-31T23:59:59',inplace=True)
    net_sta_set = set(inv_df['#Network']+'_'+inv_df['Station']) #get rid of repeated stations by joining rows info


    inv_df['StartTimeNum'] = inv_df['StartTime'].apply(lambda x: int(x.split("-")[0]+x.split("-")[1]+x.split("-")[2][0:2]))
    inv_df['EndTimeNum'] = inv_df['EndTime'].apply(lambda x: int(x.split("-")[0]+x.split("-")[1]+x.split("-")[2][0:2]))
    all_dicts=[]
    for net_sta in net_sta_set:
        net = net_sta.split("_")[0]
        sta = net_sta.split("_")[1]
        # finding all rows with same net and sta
        row = inv_df[(inv_df['#Network']==net) & (inv_df['Station']==sta)]
        row=row.reset_index()
        rowtimemax = row.loc[row['EndTimeNum'].idxmax()]
        rowtimemin = row.loc[row['StartTimeNum'].idxmin()]

        dict_row = {'#Network':net,'Station':sta,'Latitude':row.loc[0,'Latitude'],'Longitude':row.loc[0,'Longitude'],'Elevation':row.loc[0,'Elevation'],'SiteName':row.loc[0,'SiteName'],'StartTime':rowtimemin['StartTime'],'EndTime':rowtimemax['EndTime']}
        all_dicts.append(dict_row)
    new_inv_df=pd.DataFrame(all_dicts)
    # os.remove(inventorytxtfile)
    new_inv_df.to_csv(out_inventorytxtfile, index=False,sep="|")

    return out_inventorytxtfile

def measure_status(meas_file):
    if not os.path.exists(meas_file):
        f = open(meas_file, 'w')
        finished_events = np.array([])
        finished_file = np.array([])
    elif os.path.exists(meas_file):
        f = open(meas_file, 'a')
        finished_measure_df = pd.read_csv(meas_file,header=None,names=['filename','event_name'])
        finished_file = finished_measure_df['filename'].tolist()
        finished_events = finished_measure_df['event_name'].tolist()
    return f, finished_file, finished_events

def sks_measure_file_start(sks_meas_file_name,stlon,stlat,header_sks_measure):
    sks_meas_file = open(sks_meas_file_name,'w')
    sks_meas_file.write("Stlon Stlat\n")
    sks_meas_file.write("{:.4f} {:.4f}\n".format(stlon,stlat))
    sks_meas_file.write(header_sks_measure)
    return sks_meas_file