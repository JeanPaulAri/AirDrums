using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

public class TrackingDebugOverlay : MonoBehaviour
{
    [Header("Overlay Root")]
    [SerializeField] private RectTransform overlayRoot;

    [Header("Marker Sizes")]
    [SerializeField] private Vector2 zoneMarkerSize = new Vector2(28f, 28f);
    [SerializeField] private Vector2 stickMarkerSize = new Vector2(16f, 16f);

    [Header("Colors")]
    [SerializeField] private Color zoneColor = new Color(0f, 1f, 1f, 0.45f);
    [SerializeField] private Color zoneHighlightColor = new Color(1f, 1f, 0f, 0.85f);
    [SerializeField] private Color stick1Color = new Color(1f, 0.2f, 0.2f, 0.95f); // Right
    [SerializeField] private Color stick2Color = new Color(0.2f, 0.4f, 1f, 0.95f); // Left
    [SerializeField] private Color stick3Color = new Color(0.2f, 1f, 0.3f, 0.95f); // Foot

    [Header("Highlight")]
    [SerializeField] private float highlightDuration = 0.12f;

    private readonly Dictionary<string, Image> zoneImages = new Dictionary<string, Image>();
    private readonly Dictionary<int, Image> stickImages = new Dictionary<int, Image>();

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
        if (config == null)
        {
            return;
        }

        sourceWidth = Mathf.Max(1, config.dim_x);
        sourceHeight = Mathf.Max(1, config.dim_y);

        if (config.elementos == null)
        {
            return;
        }

        for (int i = 0; i < config.elementos.Length; i++)
        {
            MiddlewareZoneElement element = config.elementos[i];
            string key = NormalizeZoneKey(element.zone);

            Image img;
            if (!zoneImages.TryGetValue(key, out img))
            {
                img = CreateMarkerImage("Zone_" + key, zoneColor, zoneMarkerSize);
                zoneImages[key] = img;
            }

            img.color = zoneColor;
            PlaceMarker(img.rectTransform, element.x, element.y);
        }
    }

    public void UpdateStickPosition(int stickIndex, float sourceX, float sourceY)
    {
        Image img;
        if (!stickImages.TryGetValue(stickIndex, out img))
        {
            img = CreateMarkerImage("Stick_" + stickIndex, GetStickColor(stickIndex), stickMarkerSize);
            stickImages[stickIndex] = img;
        }

        PlaceMarker(img.rectTransform, sourceX, sourceY);
    }

    public void HighlightZone(string middlewareZone)
    {
        string key = NormalizeZoneKey(middlewareZone);

        Image img;
        if (zoneImages.TryGetValue(key, out img))
        {
            StartCoroutine(HighlightRoutine(img));
        }
    }

    private IEnumerator HighlightRoutine(Image img)
    {
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

    private void PlaceMarker(RectTransform marker, float sourceX, float sourceY)
    {
        if (overlayRoot == null)
        {
            return;
        }

        float width = overlayRoot.rect.width;
        float height = overlayRoot.rect.height;

        float posX = (sourceX / sourceWidth) * width;
        float posY = -(sourceY / sourceHeight) * height;

        marker.anchoredPosition = new Vector2(posX, posY);
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
        Texture2D tex = Texture2D.whiteTexture;
        return Sprite.Create(
            tex,
            new Rect(0, 0, tex.width, tex.height),
            new Vector2(0.5f, 0.5f)
        );
    }
}