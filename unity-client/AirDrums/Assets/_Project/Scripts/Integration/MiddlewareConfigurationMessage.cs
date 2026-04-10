using UnityEngine;
using System;

[Serializable]
public class MiddlewareConfigurationMessage
{
    public string tipo;
    public int dim_x;
    public int dim_y;
    public MiddlewareZoneElement[] elementos;
}