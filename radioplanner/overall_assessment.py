"""Conservative preliminary combined assessment of radio links."""

def assess(budget_status, terrain_status, terrain_source=None):
    if budget_status == 'red' or terrain_status == 'red':
        return 'red', 'Empfangsreserve unzureichend oder Geländehindernis erkannt'
    if terrain_status == 'unknown':
        return 'unknown', 'Gelände-/Fresnelprüfung fehlt oder ist nach Änderungen erneut erforderlich'
    if budget_status == 'unknown':
        return 'unknown', 'Link-Budget konnte nicht berechnet werden'
    if budget_status == 'yellow' or terrain_status == 'yellow':
        return 'yellow', 'Geringe Empfangsreserve oder eingeschränkte Fresnelfreiheit'
    if not terrain_source or 'online' in terrain_source.lower() or 'srtm' in terrain_source.lower():
        return 'yellow', 'Geländeprüfung mit Online-Höhendaten: nur vorläufig'
    return 'green', 'Link-Budget und lokale Gelände-/Fresnelprüfung rechnerisch frei'
