# In case of providers whose request do not follow a regular pattern, you can use code here to form it


############################################################################################################
# list of affected provider_codes
custom_url_list = tuple([x + '_NAIP' for x in (
     'AL','AR','AZ','CA','CO','CT','DE','FL','GA','IA','ID','IL',
     'IN','KS','KY','LA','MA','MD','ME','MI','MN','MO','MS','MT',
     'NC','ND','NE','NH','NJ','NM','NV','NY','OH','OK','OR','PA',
     'RI','SC','SD','TN','TX','UT','VA','VT','WA','WI','WV','WY')])
############################################################################################################


def custom_wms_request(bbox,width,height,provider):
    if '_NAIP' in provider['code']:
        (xmin,ymax,xmax,ymin)=bbox
        url="https://gis.apfo.usda.gov/arcgis/rest/services/NAIP_Historical/"+provider['code']+"/ImageServer/exportImage?f=image&bbox="+str(xmin)+"%2C"+str(ymin)+"%2C"+str(xmax)+"%2C"+str(ymax)+"&imageSR=102100&bboxSR=102100&size="+str(width)+"%2C"+str(height)
        return (url,None)

def custom_tms_request(tilematrix,til_x,til_y,provider):
    return (None,None)
