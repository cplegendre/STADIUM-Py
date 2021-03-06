import sys, os, glob, shutil
from obspy.clients.fdsn import Client
from rfsks_support.other_support import avg, date2time, write_station_file, Timeout, organize_inventory
from obspy import read_inventory
import pandas as pd
from obspy import UTCDateTime as UTC
from rf import RFStream
import numpy as np
from rfsks_support.rfsks_extras import retrieve_waveform, multi_download
from rfsks_support.plotting_map import plot_merc, station_map, events_map
import logging, yaml


with open('Settings/advRFparam.yaml') as f:
    inpRFdict = yaml.load(f, Loader=yaml.FullLoader)

## Fine tuning of SKS
with open('Settings/advSKSparam.yaml') as f:
    inpSKSdict = yaml.load(f, Loader=yaml.FullLoader)

class downloadDataclass:
    
    def __init__(self,inventoryfile,client, minlongitude,maxlongitude,minlatitude,maxlatitude,inventorytxtfile,fig_frmt="png",method='RF',channel = "BHZ,BHE,BHN"):
        self.logger = logging.getLogger(__name__)
        self.inventoryfile = inventoryfile
        self.inventorytxtfile = inventorytxtfile
        self.inv = None
        self.client = []
        if len(client) != 0:
            for cl in client:
                self.client.append(cl)
        self.minlongitude = minlongitude
        self.maxlongitude = maxlongitude
        self.minlatitude = minlatitude
        self.maxlatitude = maxlatitude
        self.clon = avg(self.minlongitude,self.maxlongitude)
        self.clat = avg(self.minlatitude,self.maxlatitude)
        self.channel = channel
        self.fig_frmt = fig_frmt
        self.method = method.upper()
        try:
            if self.method=='RF':
                self.minradius,self.maxradius=int(inpRFdict['rf_event_search_settings']['minradiusRF']),int(inpRFdict['rf_event_search_settings']['maxradiusRF'])
                # self.minradius,self.maxradius=int(inpRF.loc['minradiusRF','VALUES']),int(inpRF.loc['maxradiusRF','VALUES'])
            elif self.method=='SKS':
                self.minradius,self.maxradius=int(inpSKSdict['sks_event_search_settings']['minradiusSKS']),int(inpSKSdict['sks_event_search_settings']['maxradiusSKS'])
        except Exception as exception:
            self.logger.error(f"Illegal method input {self.method}", exc_info=True)
            sys.exit()

    ## Defining get_stnxml
    def get_stnxml(self,network='*', station="*",channel = "BHZ,BHE,BHN"):
        print("\n")
        self.logger.info('Retrieving station information')
        ninvt=0
        while ninvt < len(self.client):
            try:
                client = Client(self.client[ninvt])
            except Exception as e:
                self.logger.error(f"No FDSN services could be discovered for {self.client[ninvt]}! Try again after some time.")
                sys.exit()
            self.logger.info(f'from {self.client[ninvt]}')
            try:
                invt = client.get_stations(network=network, station=station, channel=self.channel, level='channel',minlongitude=self.minlongitude, maxlongitude=self.maxlongitude,minlatitude=self.minlatitude, maxlatitude=self.maxlatitude)
                inventory = invt
                break
            except Exception as exception:
                self.logger.error(f"No stations found for the given parameters for {self.client[ninvt]}", exc_info=True)
            ninvt+=1
            # sys.exit()
        if len(self.client)>1:
            for cl in self.client[ninvt+1:]:
                self.logger.info(f'from {cl}')
                try:
                    client = Client(cl)
                    invt = client.get_stations(network=network, station=station, channel=self.channel, level='channel',minlongitude=self.minlongitude, maxlongitude=self.maxlongitude,minlatitude=self.minlatitude, maxlatitude=self.maxlatitude)
                    inventory +=invt
                except Exception as exception:
                    self.logger.warning(f"FDSNNoDataException for {cl}")
        # self.logger.info(inventory)

        inventory.write(self.inventoryfile, 'STATIONXML')
        self.inv = inventory
        inventory.write(self.inventorytxtfile, 'STATIONTXT',level='station')
        organize_inventory(self.inventorytxtfile)
        # self.inventorytxtfile = organize_inventory(self.inventorytxtfile)
        

    ## inventory_catalog
    def obtain_events(self, catalogxmlloc,catalogtxtloc,minmagnitude=5.5,maxmagnitude=9.5):

        ## Check for the station information
        if os.path.exists(self.inventorytxtfile):
            invent_df = pd.read_csv(self.inventorytxtfile,sep="|",keep_default_na=False, na_values=[""])
            total_stations = invent_df.shape[0]
            if invent_df.shape[0]==0:
                self.logger.error("No data available, exiting...")
                sys.exit()
        else:
            self.logger.error("No data available, exiting...")
            sys.exit()


        tot_evnt_stns = 0
        if not self.inv:
            self.logger.info("Reading station inventory to obtain events catalog")
            try:
                # Read the station inventory
                self.inv = read_inventory(self.inventoryfile, format="STATIONXML")
            except Exception as exception:
                self.logger.error("No available data", exc_info=True)
                sys.exit()
        # list all the events during the station active time
        self.staNamesNet,staLats,staLons=[],[],[]
        count = 1
        for net in self.inv:
            for sta in net:
                network = net.code #network name
                station = sta.code #station name
                print("\n")
                self.logger.info(f"{count}/{total_stations} Retrieving event info for {network}-{station}")
                count+=1
                self.staNamesNet.append(f"{network}_{station}")

                sta_lat = sta.latitude #station latitude
                staLats.append(sta_lat)

                sta_lon = sta.longitude #station longitude
                staLons.append(sta_lon)

                sta_sdate = sta.start_date #station start date
                sta_edate = sta.end_date #station end date
                # sta_edate_str = sta_edate
                if not sta_edate:
                    sta_edate = UTC("2599-12-31T23:59:59")
                    # sta_edate_str = "2599-12-31T23:59:59"

                stime, etime = date2time(sta_sdate,sta_edate) #station start and end time in UTC


                catalogxml = catalogxmlloc+f'{network}-{station}-{sta_sdate.year}-{sta_edate.year}-{self.method}-{self.method}_events.xml' #xml catalog
                # self.allcatalogxml.append(catalogxml)
                catalogtxt = catalogtxtloc+f'{network}-{station}-{sta_sdate.year}-{sta_edate.year}-events-info-{self.method}.txt' #txt catalog
                if not os.path.exists(catalogxml) and not os.path.exists(catalogtxt):
                    self.logger.info(f"Obtaining catalog: {self.method}: {network}-{station}-{sta_sdate.year}-{sta_edate.year}")
                    kwargs = {'starttime': stime, 'endtime': etime, 
                                    'latitude': sta_lat, 'longitude': sta_lon,
                                    'minradius': self.minradius, 'maxradius': self.maxradius,
                                    'minmagnitude': minmagnitude, 'maxmagnitude': maxmagnitude}
                    client = Client('IRIS')

                    try:
                        catalog = client.get_events(**kwargs)
                    except:
                        self.logger.warning("ConnectionResetError while obtaining the events from the client - IRIS")
                        continue
                    catalog.write(catalogxml, 'QUAKEML') #writing xml catalog

                    
                    tot_evnt_stns += len(catalog)

                    evtimes,evlats,evlons,evdps,evmgs,evmgtps=[],[],[],[],[],[]
                    self.logger.info("Writing the event data into a text file")

                    with open(catalogtxt, 'w') as f:
                        f.write('evtime,evlat,evlon,evdp,evmg\n')
                        for cat in catalog:
                            try:
                                try:
                                    evtime,evlat,evlon,evdp,evmg,evmgtp=cat.origins[0].time,cat.origins[0].latitude,cat.origins[0].longitude,cat.origins[0].depth/1000,cat.magnitudes[0].mag,cat.magnitudes[0].magnitude_type
                                except:
                                    evtime,evlat,evlon,evdp,evmg,evmgtp=cat.origins[0].time,cat.origins[0].latitude,cat.origins[0].longitude,cat.origins[0].depth/1000,cat.magnitudes[0].mag,"Mww"
                                evtimes.append(str(evtime))
                                evlats.append(float(evlat))
                                evlons.append(float(evlon))
                                evdps.append(float(evdp))
                                evmgs.append(float(evmg))
                                evmgtps.append(str(evmgtp))
                                f.write('{},{:.4f},{:.4f},{:.1f},{:.1f}\n'.format(evtime,evlat,evlon,evdp,evmg)) #writing txt catalog
                                
                            except Exception as exception:
                                self.logger.warning(f"Unable to write for {evtime}")
                    self.logger.info("Finished writing the event data into a text and xml file")
                else:
                    self.logger.info(f"{catalogxml.split('/')[-1]} and {catalogtxt.split('/')[-1]} already exists!")

        ################################## Download
    def download_data(self,catalogtxtloc,datafileloc,tot_evnt_stns,rem_evnts, plot_stations=True, plot_events=True,dest_map="./",locations=[""]):
     
        self.logger.info(f"Total data files to download: {tot_evnt_stns}")
        rem_dl = rem_evnts
        succ_dl,num_try = 0, 0 
        rf_stalons,sks_stalons = [],[]
        rf_stalats, sks_stalats = [], []
        rf_staNetNames, sks_staNetNames = [],[]

        all_stns_df = pd.read_csv(self.inventorytxtfile,sep="|")

        all_sta_lats=all_stns_df['Latitude'].values
        all_sta_lons=all_stns_df['Longitude'].values
        all_sta_nms=all_stns_df['Station'].values
        all_sta_nets=all_stns_df['#Network'].values
        
        sta_str_list = []
        #Retrive waveform data for the events
        for slat,slon,stn,net in zip(all_sta_lats,all_sta_lons,all_sta_nms,all_sta_nets):
            sta_str = f"{net}-{stn}-{slon}-{slat}"
            if sta_str in sta_str_list:
                continue
            else:
                sta_str_list.append(sta_str)

            catfile = catalogtxtloc+f"{net}-{stn}-events-info-{self.method}.txt"
            cattxtnew = catalogtxtloc+f"{net}-{stn}-events-info-available-{self.method}.txt"
            
            if self.method == 'RF':
                print("\n")
                self.logger.info(f"Searching and downloading data for {self.method}; {net}-{stn}")
                rfdatafile = datafileloc+f"{net}-{stn}-{str(inpRFdict['filenames']['data_rf_suffix'])}.h5"
                # rfdatafile = datafileloc+f"{net}-{stn}-{str(inpRF.loc['data_rf_suffix','VALUES'])}.h5"
                # rfdatafile = datafileloc+f"{net}-{stn}-{str(inpRF.loc['data_rf_suffix','VALUES'])}.h5"
                if os.path.exists(catfile) and not os.path.exists(rfdatafile) and tot_evnt_stns > 0:
                    stream = RFStream()
                    df = pd.read_csv(catfile,sep=",")
                    # df = pd.read_csv(catfile,delimiter="\||,", names=['evtime','evlat','evlon','evdp','evmg','evmgtp'],header=None,engine="python")
                    evmg = df['evmg'].values
                    evmgtp = ["Mww" for val in df['evmg']]
                    # evmg = [float(val.split()[0]) for val in df['evmg']]
                    # evmgtp = [str(val.split()[1]) for val in df['evmg']]
                    
                    fcat = open(cattxtnew,'w')
                    for evtime,evdp,elat,elon,em,emt in zip(df['evtime'],df['evdp'],df['evlat'],df['evlon'],evmg,evmgtp):
                        rem_dl -= 1
                        num_try += 1
                        
                        strm,res,msg = multi_download(self.client,self.inv,net,stn,slat,slon,elat,elon,evdp,evtime,em,emt,fcat,stalons=rf_stalons,stalats=rf_stalats,staNetNames=rf_staNetNames,phase='P',locations=locations)
                        if res:
                            succ_dl+=1
                            
                        if not msg:
                            self.logger.info(f"Event: {evtime}; rem: {rem_dl}/{tot_evnt_stns}; dl: {succ_dl}/{num_try}")
                        else:
                            self.logger.info(f"{msg}; rem: {rem_dl}/{tot_evnt_stns}; dl: {succ_dl}/{num_try}")

                        if strm:
                            stream.extend(strm)

                    if not len(stream):
                        self.logger.warning(f"No data {rfdatafile}")
                    stream.write(rfdatafile, 'H5')
                    fcat.close()
                ### Event map plot
                # cattxtnew = catalogtxtloc+f'{net}-{stn}-events-info-rf.txt'
                if os.path.exists(cattxtnew) and plot_events and not os.path.exists(f"{net}-{stn}-{str(inpRFdict['filenames']['events_map_suffix'])}.png"):
                # if os.path.exists(cattxtnew) and plot_events and not os.path.exists(f"{net}-{stn}-{str(inpRF.loc['events_map_suffix','VALUES'])}.png"):
                    df = pd.read_csv(cattxtnew,delimiter="\||,", names=['evtime','evlat','evlon','evdp','evmg','client'],header=None,engine="python")
                    if df.shape[0]:
                        evmg = [float(val.split()[0]) for val in df['evmg']]
                        event_plot_name=f"{net}-{stn}-{str(inpRFdict['filenames']['events_map_suffix'])}"
                        # event_plot_name=f"{net}-{stn}-{str(inpRF.loc['events_map_suffix','VALUES'])}"
                        if not os.path.exists(dest_map+event_plot_name+f".{self.fig_frmt}"):
                            self.logger.info(f"Plotting events map "+event_plot_name+f".{self.fig_frmt}")
                            events_map(evlons=df['evlon'], evlats=df['evlat'], evmgs=evmg, evdps=df['evdp'], stns_lon=slon, stns_lat=slat, destination=dest_map,figfrmt=self.fig_frmt, clon = slon , outname=f'{event_plot_name}')
        

                        
            if self.method == 'SKS':
                print("\n")
                self.logger.info(f"Searching and downloading data for {self.method}; {net}-{stn}")

                sksdatafile = datafileloc+f"{net}-{stn}-{str(inpSKSdict['filenames']['data_sks_suffix'])}.h5"
                if os.path.exists(catfile) and not os.path.exists(sksdatafile) and tot_evnt_stns > 0:
                    self.logger.info("Reading events catalog file")
                    stream = RFStream()

                    df = pd.read_csv(catfile,sep=",")
                    # df = pd.read_csv(catfile,delimiter="\||,", names=['evtime','evlat','evlon','evdp','evmg','evmgtp'],header=None,engine="python")
                    evmg = df['evmg'].values
                    evmgtp = ["Mww" for val in df['evmg']]
                    # evmg = [float(val.split()[0]) for val in df['evmg']]
                    # evmgtp = [str(val.split()[1]) for val in df['evmg']]
                    # cattxtnew = catalogtxtloc+f'{net}-{stn}-events-info-sks.txt'
                    fcat = open(cattxtnew,'w')
                    for i,evtime,evdp,elat,elon,em,emt in zip(range(len(df['evtime'])),df['evtime'],df['evdp'],df['evlat'],df['evlon'],evmg,evmgtp):
                        rem_dl -= 1
                        num_try += 1
                        # self.logger.info(f"Event: {evtime}; rem: {rem_dl}/{tot_evnt_stns}; dl: {succ_dl}/{num_try}")
                        strm,res,msg = multi_download(self.client,self.inv,net,stn,slat,slon,elat,elon,evdp,evtime,em,emt,fcat,stalons = sks_stalons,stalats = sks_stalats,staNetNames = sks_staNetNames,phase='SKS',locations=locations)
                        if not msg:
                            self.logger.info(f"Event: {evtime}; rem: {rem_dl}/{tot_evnt_stns}; dl: {succ_dl}/{num_try}")
                        else:
                            self.logger.info(f"{msg}; rem: {rem_dl}/{tot_evnt_stns}; dl: {succ_dl}/{num_try}")


                        if strm:
                            stream.extend(strm)
                        if res:
                            succ_dl+=1
                    if not len(stream):
                        self.logger.warning(f"No data {sksdatafile}")

                    stream.write(sksdatafile, 'H5')
                    fcat.close()
                else:
                    if os.path.exists(catfile):
                        self.logger.info(f"catalog {catfile} exists!")
                    else:
                        self.logger.info(f"catalog {catfile} does not exist!")
                    if not os.path.exists(sksdatafile):
                        self.logger.info(f"datafile {sksdatafile} does not exist!")
                    if tot_evnt_stns > 0:
                        self.logger.info(f"Total files to download {tot_evnt_stns}")
                    
                ### Event map plot
                # cattxtnew = catalogtxtloc+f'{net}-{stn}-events-info-sks.txt'
                if os.path.exists(cattxtnew) and plot_events and not os.path.exists(f"{net}-{stn}-{str(inpSKSdict['filenames']['events_map_suffix'])}.png"):
                    df = pd.read_csv(cattxtnew,delimiter="\||,", names=['evtime','evlat','evlon','evdp','evmg','client'],header=None,engine="python")
                    if df.shape[0]:
                        evmg = [float(val.split()[0]) for val in df['evmg']]
                        event_plot_name=f"{net}-{stn}-{str(inpSKSdict['filenames']['events_map_suffix'])}"
                        if not os.path.exists(dest_map+event_plot_name+f".{self.fig_frmt}"):
                            self.logger.info(f"Plotting events map "+event_plot_name+f".{self.fig_frmt}")
                            events_map(evlons=df['evlon'], evlats=df['evlat'], evmgs=evmg, evdps=df['evdp'], stns_lon=slon, stns_lat=slat, destination=dest_map,figfrmt=self.fig_frmt, clon = slon , outname=f'{net}-{stn}-SKS')

        ## plot station map for all the stations for which the data has been successfully retrieved
        if plot_stations and self.method == 'RF' and len(rf_stalons):
            print("\n")
            self.logger.info("Plotting station map for RF")
            map = plot_merc(resolution='h',llcrnrlon=self.minlongitude-1, llcrnrlat=self.minlatitude-1,urcrnrlon=self.maxlongitude+1, urcrnrlat=self.maxlatitude+1,topo=True)
            station_map(map, stns_lon=rf_stalons, stns_lat=rf_stalats,stns_name= rf_staNetNames,figname=str(inpRFdict['filenames']['retr_station_prefix']), destination=dest_map,figfrmt=self.fig_frmt)
            # station_map(map, stns_lon=rf_stalons, stns_lat=rf_stalats,stns_name= rf_staNetNames,figname=str(inpRF.loc['retr_station_prefix','VALUES']), destination=dest_map,figfrmt=self.fig_frmt)


        if plot_stations and self.method == 'SKS' and len(sks_stalons):
            print("\n")
            self.logger.info("Plotting station map for SKS")
            map = plot_merc(resolution='h',llcrnrlon=self.minlongitude-1, llcrnrlat=self.minlatitude-1,urcrnrlon=self.maxlongitude+1, urcrnrlat=self.maxlatitude+1,topo=True)
            station_map(map, stns_lon=sks_stalons, stns_lat=sks_stalats,stns_name= sks_staNetNames,figname=str(inpSKSdict['filenames']['retr_station_prefix']), destination=dest_map,figfrmt=self.fig_frmt)
        ## Write the retrieved station catalog
        if self.method == 'RF':
            write_station_file(self.inventorytxtfile,rf_staNetNames,outfile=catalogtxtloc+str(inpRFdict['filenames']['retr_stations']))
            # write_station_file(self.inventorytxtfile,rf_staNetNames,outfile=catalogtxtloc+str(inpRF.loc['retr_stations','VALUES']))
        elif self.method == 'SKS':
            write_station_file(self.inventorytxtfile,sks_staNetNames,outfile=catalogtxtloc+str(inpSKSdict['filenames']['retr_stations']))