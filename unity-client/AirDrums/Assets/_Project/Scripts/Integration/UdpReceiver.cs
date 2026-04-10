using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

public class UdpReceiver : MonoBehaviour
{
    [Header("UDP Config")]
    [SerializeField] private int listenPort = 5052;
    [SerializeField] private bool autoStart = true;

    public event Action<string> OnMessageReceived;

    private UdpClient udpClient;
    private IPEndPoint remoteEndPoint;
    private bool isRunning;

    public bool IsRunning => isRunning;
    public int ListenPort => listenPort;

    private void Start()
    {
        if (autoStart)
        {
            StartReceiver();
        }
    }

    public void StartReceiver()
    {
        if (isRunning) return;

        try
        {
            remoteEndPoint = new IPEndPoint(IPAddress.Any, listenPort);
            udpClient = new UdpClient(listenPort);
            isRunning = true;

            Debug.Log($"[UdpReceiver] Escuchando en UDP {listenPort}");
            udpClient.BeginReceive(OnUdpData, null);
        }
        catch (Exception ex)
        {
            Debug.LogError($"[UdpReceiver] No se pudo iniciar: {ex.Message}");
            isRunning = false;
        }
    }

    public void StopReceiver()
    {
        isRunning = false;

        try
        {
            udpClient?.Close();
            udpClient = null;
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[UdpReceiver] Error al cerrar socket: {ex.Message}");
        }
    }

    private void OnUdpData(IAsyncResult result)
    {
        if (!isRunning || udpClient == null) return;

        try
        {
            byte[] data = udpClient.EndReceive(result, ref remoteEndPoint);
            string message = Encoding.UTF8.GetString(data);

            OnMessageReceived?.Invoke(message);
        }
        catch (ObjectDisposedException)
        {
            return;
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[UdpReceiver] Error recibiendo mensaje: {ex.Message}");
        }
        finally
        {
            if (isRunning && udpClient != null)
            {
                try
                {
                    udpClient.BeginReceive(OnUdpData, null);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[UdpReceiver] No se pudo rearmar BeginReceive: {ex.Message}");
                }
            }
        }
    }

    private void OnDestroy()
    {
        StopReceiver();
    }
}