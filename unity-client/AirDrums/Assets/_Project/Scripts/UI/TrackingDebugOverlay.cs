using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

public class TrackingDebugOverlay : MonoBehaviour
{
    [System.Serializable]
    public struct ZoneOverlayLayout
    {
        public string zoneKey;
        public Vector2 sourceCenter;
        public Vector2 sourceSize;
        public Vector2 viewportCenter; // 0..1 en pantalla
        public Vector2 viewportSize;   // 0..1 relativo a la pantalla
    }

    [Header("Overlay Root")]
    [SerializeField] private RectTransform overlayRoot;

    [Header("Marker Size")]
    [SerializeField] private Vector2 stickMarkerSize = new Vector2(16f, 16f);

    [Header("Colors")]
    [SerializeField] private Color zoneColor = new Color(0f, 1f, 1f, 0.45f);
    [SerializeField] private Color zoneHighlightColor = new Color(1f, 1f, 0f, 0.85f);
    [SerializeField] private Color stick1Color = new Color(1f, 0.2f, 0.2f, 0.95f);
    [SerializeField] private Color stick2Color = new Color(0.2f, 0.4f, 1f, 0.95f);
    [SerializeField] private Color stick3Color = new Color(0.2f, 1f, 0.3f, 0.95f);

    [Header("Highlight")]
    [SerializeField] private float highlightDuration = 0.12f;

    private readonly Dictionary<string, Image> zoneImages = new Dictionary<string, Image>();
    private readonly Dictionary<int, Image> stickImages = new Dictionary<int, Image>();
    private readonly Dictionary<string, ZoneOverlayLayout> zoneLayouts = new Dictionary<string, ZoneOverlayLayout>();
    
    private readonly Dictionary<int, Vector2> stickViewportCenters = new Dictionary<int, Vector2>();

    private int sourceWidth = 640;
    private int sourceHeight = 480;

    private Sprite markerSprite;

    private void Awake()
    {
        if (overlayRoot == null)
        {
            overlayRoot = GetComponent<RectTransform>();
        }

        markerSprite = CreateRuntimeWhiteSprite();
    }

    public void SetConfiguration(MiddlewareConfigurationMessage config)
    {
        if (config == null || config.elementos == null)
        {
            return;
        }

        sourceWidth = Mathf.Max(1, config.dim_x);
        sourceHeight = Mathf.Max(1, config.dim_y);

        zoneLayouts.Clear();

        float rootWidth = overlayRoot.rect.width;
        float rootHeight = overlayRoot.rect.height;

        float scale = Mathf.Min(rootWidth / sourceWidth, rootHeight / sourceHeight);
        float renderWidth = sourceWidth * scale;
        float renderHeight = sourceHeight * scale;

        float offsetX = (rootWidth - renderWidth) * 0.5f;
        float offsetY = (rootHeight - renderHeight) * 0.5f;

        foreach (MiddlewareZoneElement element in config.elementos)
        {
            if (element == null || string.IsNullOrWhiteSpace(element.zone))
            {
                continue;
            }

            string key = NormalizeZoneKey(element.zone);
            Vector2 sourceSize = GetZoneSourceSize(key);
            Vector2 overlaySize = sourceSize * scale;

            Image img;
            if (!zoneImages.TryGetValue(key, out img))
            {
                img = CreateMarkerImage("Zone_" + key, zoneColor, overlaySize);
                zoneImages[key] = img;
            }

            img.color = zoneColor;
            img.rectTransform.sizeDelta = overlaySize;
            PlaceMarker(img.rectTransform, element.x, element.y, offsetX, offsetY, renderWidth, renderHeight);

            float posX = offsetX + ((element.x / (float)sourceWidth) * renderWidth);
            float posY = offsetY + ((element.y / (float)sourceHeight) * renderHeight);

            // Viewport usa origen abajo-izquierda, UI usa arriba-izquierda
            Vector2 viewportCenter = new Vector2(
                posX / rootWidth,
                1f - (posY / rootHeight)
            );

            Vector2 viewportSize = new Vector2(
                overlaySize.x / rootWidth,
                overlaySize.y / rootHeight
            );

            zoneLayouts[key] = new ZoneOverlayLayout
            {
                zoneKey = key,
                sourceCenter = new Vector2(element.x, element.y),
                sourceSize = sourceSize,
                viewportCenter = viewportCenter,
                viewportSize = viewportSize
            };
        }
    }

    public bool TryGetZoneLayout(string zone, out ZoneOverlayLayout layout)
    {
        return zoneLayouts.TryGetValue(NormalizeZoneKey(zone), out layout);
    }
    
    public bool TryGetStickViewport(int stickIndex, out Vector2 viewportCenter)
    {
        return stickViewportCenters.TryGetValue(stickIndex, out viewportCenter);
    }

    public void UpdateStickPosition(int stickIndex, float sourceX, float sourceY)
    {
        if (overlayRoot == null)
        {
            return;
        }

        float rootWidth = overlayRoot.rect.width;
        float rootHeight = overlayRoot.rect.height;

        float scale = Mathf.Min(rootWidth / sourceWidth, rootHeight / sourceHeight);
        float renderWidth = sourceWidth * scale;
        float renderHeight = sourceHeight * scale;

        float offsetX = (rootWidth - renderWidth) * 0.5f;
        float offsetY = (rootHeight - renderHeight) * 0.5f;

        Image img;
        if (!stickImages.TryGetValue(stickIndex, out img))
        {
            img = CreateMarkerImage("Stick_" + stickIndex, GetStickColor(stickIndex), stickMarkerSize);
            stickImages[stickIndex] = img;
        }

        PlaceMarker(img.rectTransform, sourceX, sourceY, offsetX, offsetY, renderWidth, renderHeight);

        float posX = offsetX + ((sourceX / sourceWidth) * renderWidth);
        float posY = offsetY + ((sourceY / sourceHeight) * renderHeight);

        Vector2 viewportCenter = new Vector2(
            posX / rootWidth,
            1f - (posY / rootHeight)
        );

        stickViewportCenters[stickIndex] = viewportCenter;
    }

    public void HighlightZone(string zone)
    {
        string key = NormalizeZoneKey(zone);

        if (zoneImages.TryGetValue(key, out Image img))
        {
            StartCoroutine(HighlightRoutine(img));
        }
    }

    private IEnumerator HighlightRoutine(Image img)
    {
        if (img == null)
        {
            yield break;
        }

        img.color = zoneHighlightColor;
        yield return new WaitForSeconds(highlightDuration);

        if (img != null)
        {
            img.color = zoneColor;
        }
    }

    private Image CreateMarkerImage(string objectName, Color color, Vector2 size)
    {
        GameObject go = new GameObject(objectName, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
        go.transform.SetParent(overlayRoot, false);

        RectTransform rt = go.GetComponent<RectTransform>();
        rt.anchorMin = new Vector2(0f, 1f);
        rt.anchorMax = new Vector2(0f, 1f);
        rt.pivot = new Vector2(0.5f, 0.5f);
        rt.sizeDelta = size;

        Image img = go.GetComponent<Image>();
        img.sprite = markerSprite;
        img.color = color;
        img.raycastTarget = false;

        return img;
    }

    private void PlaceMarker(
        RectTransform marker,
        float sourceX,
        float sourceY,
        float offsetX,
        float offsetY,
        float renderWidth,
        float renderHeight)
    {
        if (overlayRoot == null)
        {
            return;
        }

        float posX = offsetX + ((sourceX / sourceWidth) * renderWidth);
        float posY = offsetY + ((sourceY / sourceHeight) * renderHeight);

        marker.anchoredPosition = new Vector2(posX, -posY);
    }

    private Vector2 GetZoneSourceSize(string zoneKey)
    {
        // Perfiles fijos tomados de main.py
        switch (zoneKey)
        {
            case "platillo": return new Vector2(110f, 60f);
            case "tom superior": return new Vector2(100f, 50f);
            case "hithat": return new Vector2(110f, 30f);
            case "tarola": return new Vector2(140f, 50f);
            case "tom inferior": return new Vector2(110f, 30f);
            case "bombo": return new Vector2(110f, 30f);
            default: return new Vector2(90f, 40f);
        }
    }

    private string NormalizeZoneKey(string zone)
    {
        if (string.IsNullOrWhiteSpace(zone))
        {
            return string.Empty;
        }

        return string.Join(" ", zone.Trim().ToLowerInvariant().Split(' '));
    }

    private Color GetStickColor(int stickIndex)
    {
        switch (stickIndex)
        {
            case 1: return stick1Color;
            case 2: return stick2Color;
            case 3: return stick3Color;
            default: return Color.white;
        }
    }

    private Sprite CreateRuntimeWhiteSprite()
    {
        Texture2D tex = new Texture2D(2, 2, TextureFormat.ARGB32, false);
        tex.SetPixels(new[] { Color.white, Color.white, Color.white, Color.white });
        tex.Apply();

        return Sprite.Create(
            tex,
            new Rect(0, 0, tex.width, tex.height),
            new Vector2(0.5f, 0.5f)
        );
    }
}