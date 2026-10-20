// Utilitário do Tulão — frontend (vanilla JS, sem build step)
// Fala com o backend Python via window.pywebview.api.*
// Visual e componentes (Dropdown/Slider) espelham o layout aprovado no artifact.

(function () {
  "use strict";

  const estado = {
    gif: { caminho: null, saida: null },
    audio: { caminho: null },
    youtube: { info: null },
    social: { info: null },
    pinterest: { titulo: null, imagens: [] },
    fontes: { arquivos: [] }, // [{caminho, nome, peso, subfamilia}]
    renomear: {
      pasta: null,
      arquivos: [],
      filtro: "",
      subAba: "montar",
      blocks: [{ tipo: "Nome atual" }],
      retirar: { buscar: "", case: false },
      substituir: { buscar: "", trocar: "", case: false },
      manterExt: true,
      drag: null,
    },
  };

  // --------------------------------------------------------------- utils

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $all(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }
  function el(tag, props, children) {
    const n = document.createElement(tag);
    if (props) for (const k in props) {
      if (k === "class") n.className = props[k];
      else if (k === "text") n.textContent = props[k];
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), props[k]);
      else n.setAttribute(k, props[k]);
    }
    (children || []).forEach(c => n.appendChild(c));
    return n;
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("mostrando");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("mostrando"), 2600);
  }

  function status(sel, msg, tipo) {
    const n = $(sel);
    n.textContent = msg || "";
    n.className = "status" + (tipo ? " " + tipo : "");
  }

  function api() {
    if (!window.pywebview || !window.pywebview.api) throw new Error("Backend ainda não carregou.");
    return window.pywebview.api;
  }

  async function chamar(metodo, ...args) {
    const r = await api()[metodo](...args);
    if (r && typeof r === "object" && "erro" in r) throw new Error(r.erro);
    return r;
  }

  const ouvintes = {};
  window.tulao = {
    emit(evento, dados) { (ouvintes[evento] || []).forEach(fn => { try { fn(dados); } catch (e) { console.error(e); } }); },
    on(evento, fn) { (ouvintes[evento] = ouvintes[evento] || []).push(fn); },
  };

  // --------------------------------------------------------- componente: Dropdown

  const dropdownsAbertos = [];

  function criarDropdown(container, opts) {
    // opts: { value, options:[string], onSelect(v), width }
    let value = opts.value;
    let options = opts.options || [];
    const raiz = el("div", { class: "dd" });
    if (opts.width) raiz.style.maxWidth = opts.width + "px";
    const btn = el("button", { type: "button", class: "dd-btn" });
    const span = el("span", { text: value || "" });
    const seta = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    seta.setAttribute("width", "12"); seta.setAttribute("height", "8"); seta.setAttribute("viewBox", "0 0 12 8");
    seta.innerHTML = '<path d="M1 1L6 6L11 1" stroke="#555555" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
    btn.appendChild(span); btn.appendChild(seta);
    const painel = el("div", { class: "dd-painel" });
    raiz.appendChild(btn); raiz.appendChild(painel);
    container.innerHTML = "";
    container.appendChild(raiz);

    function fechar() { raiz.classList.remove("aberto"); }
    function fecharOutros() { dropdownsAbertos.forEach(f => { if (f !== fechar) f(); }); }

    function render() {
      span.textContent = value || "";
      painel.innerHTML = "";
      options.forEach(label => {
        const opt = el("div", {
          class: "dd-opt" + (label === value ? " selecionada" : ""), text: label,
          onclick: () => { value = label; fechar(); render(); if (opts.onSelect) opts.onSelect(label); },
        });
        painel.appendChild(opt);
      });
    }

    btn.addEventListener("click", e => {
      e.stopPropagation();
      const abrindo = !raiz.classList.contains("aberto");
      fecharOutros();
      raiz.classList.toggle("aberto", abrindo);
    });

    if (!dropdownsAbertos.includes(fechar)) dropdownsAbertos.push(fechar);
    render();

    return {
      setValue(v) { value = v; render(); },
      setOptions(opts2, novoValor) { options = opts2; if (novoValor !== undefined) value = novoValor; render(); },
      getValue() { return value; },
    };
  }

  document.addEventListener("click", () => dropdownsAbertos.forEach(f => f()));

  // ----------------------------------------------------------- componente: Slider

  function criarSlider(container, opts) {
    // opts: { min, max, value, suffix, onChange }
    const linha = el("div", { class: "slider-linha" });
    const input = el("input", { type: "range", class: "tulao-slider", min: opts.min, max: opts.max });
    input.value = opts.value;
    const valor = el("span", { class: "valor", text: opts.value + (opts.suffix || "") });
    input.addEventListener("input", () => {
      valor.textContent = input.value + (opts.suffix || "");
      if (opts.onChange) opts.onChange(Number(input.value));
    });
    linha.appendChild(input); linha.appendChild(valor);
    container.innerHTML = "";
    container.appendChild(linha);
    return {
      getValue() { return Number(input.value); },
      setValue(v) { input.value = v; valor.textContent = v + (opts.suffix || ""); },
    };
  }

  // --------------------------------------------------------------- navegação

  function irParaAba(aba) {
    $all(".aba-btn").forEach(b => b.classList.toggle("ativa", b.dataset.aba === aba));
    $all(".pagina").forEach(p => p.classList.toggle("ativa", p.id === "pagina-" + aba));
  }

  $all(".aba-btn").forEach(btn => {
    btn.addEventListener("click", () => irParaAba(btn.dataset.aba));
  });

  // Evita que soltar um arquivo fora de uma dropzone faça a janela "navegar"
  // pro arquivo (comportamento padrão do WebKit).
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => e.preventDefault());

  // --------------------------------------------------------------- dropzone genérica

  // O WebKit não entrega o caminho real de um arquivo arrastado da Finder
  // (por segurança — nenhum navegador entrega). Em vez de insistir nisso,
  // o arrastar vira um atalho pro mesmo seletor de arquivo do clique: você
  // ainda pode simplesmente soltar o arquivo aqui, só que por baixo dos
  // panos ele abre o seletor (geralmente já na pasta certa) em vez de usar
  // o arquivo arrastado diretamente.
  function ligarDropzone(zonaSel, aoEscolher) {
    const zona = $(zonaSel);
    zona.addEventListener("click", async () => {
      const caminho = await chamar("escolher_arquivo", null, false);
      if (caminho) aoEscolher(caminho);
    });
    zona.addEventListener("dragover", e => { e.preventDefault(); zona.classList.add("arrastando"); });
    zona.addEventListener("dragleave", () => zona.classList.remove("arrastando"));
    zona.addEventListener("drop", async e => {
      e.preventDefault();
      zona.classList.remove("arrastando");
      const caminho = await chamar("escolher_arquivo", null, false);
      if (caminho) aoEscolher(caminho);
    });
  }

  // ================================================================= GIF

  const ddGifDither = criarDropdown($("#gif-dither-dd"), {
    value: "Bayer (leve, recomendado)",
    options: ["Bayer (leve, recomendado)", "Sem dithering (mais leve)", "Floyd-Steinberg (pesado)", "Sierra (qualidade máxima)"],
    onSelect: atualizarDetalheGif,
  });
  const slGifFps = criarSlider($("#gif-fps-slider"), { min: 10, max: 24, value: 15, onChange: atualizarDetalheGif });
  const slGifOtim = criarSlider($("#gif-otim-slider"), { min: 0, max: 100, value: 50, suffix: "%", onChange: atualizarDetalheGif });
  const slGifCores = criarSlider($("#gif-cores-slider"), { min: 32, max: 256, value: 256, onChange: atualizarDetalheGif });

  function labelDitherCurto(v) {
    if (v.startsWith("Bayer")) return "Bayer (leve)";
    if (v.startsWith("Sem")) return "sem dithering";
    if (v.startsWith("Floyd")) return "Floyd-Steinberg";
    return "Sierra";
  }

  function atualizarDetalheGif() {
    $("#gif-detalhe").textContent =
      `${slGifFps.getValue()} fps · ${slGifCores.getValue()} cores · ${labelDitherCurto(ddGifDither.getValue())}`;
  }
  atualizarDetalheGif();

  ligarDropzone("#gif-dropzone", caminho => {
    estado.gif.caminho = caminho;
    $("#gif-dropzone-txt").textContent = caminho.split("/").pop() + "\n(arraste outro para trocar)";
    $("#gif-caixa").innerHTML = "";
    $("#gif-caixa").textContent = "Clique em Converter pra ver a prévia aqui";
    $("#gif-tamanho").textContent = "Tamanho estimado: —";
    $("#gif-abrir-pasta").style.display = "none";
    status("#gif-status", "Pronto.");
  });

  $("#gif-original").addEventListener("click", () => {
    $("#gif-largura").value = ""; $("#gif-altura").value = "";
  });

  $("#gif-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.gif.pasta = p; $("#gif-pasta-txt").textContent = p; }
  });

  $("#gif-converter").addEventListener("click", async () => {
    if (!estado.gif.caminho) return toast("Escolha um vídeo primeiro.");
    const btn = $("#gif-converter");
    btn.disabled = true;
    $("#gif-barra").style.width = "30%";
    status("#gif-status", "Convertendo… pode levar alguns segundos.");
    try {
      const opcoes = {
        fps: slGifFps.getValue(), otimizacao: slGifOtim.getValue(), cores: slGifCores.getValue(),
        dither: ddGifDither.getValue(),
        largura: $("#gif-largura").value ? parseInt($("#gif-largura").value) : null,
        altura: $("#gif-altura").value ? parseInt($("#gif-altura").value) : null,
        pasta: estado.gif.pasta || null,
      };
      const r = await chamar("gif_converter", estado.gif.caminho, opcoes);
      $("#gif-barra").style.width = "100%";
      status("#gif-status", "Pronto!", "ok");
      const img = el("img", {});
      img.src = "file://" + r.caminho + "?t=" + Date.now();
      $("#gif-caixa").innerHTML = ""; $("#gif-caixa").appendChild(img);
      $("#gif-tamanho").textContent = "Tamanho: " + r.tamanho_fmt;
      $("#gif-abrir-pasta").style.display = "";
      estado.gif.saida = r.caminho;
    } catch (e) {
      status("#gif-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
      setTimeout(() => { $("#gif-barra").style.width = "0%"; }, 600);
    }
  });

  $("#gif-abrir-pasta").addEventListener("click", () => {
    if (estado.gif.saida) chamar("abrir_pasta_no_finder", estado.gif.saida);
  });

  // ================================================================ ÁUDIO

  const ddAudioFormato = criarDropdown($("#audio-formato-dd"), { value: "MP3", options: ["MP3"] });
  const ddAudioTaxa = criarDropdown($("#audio-taxa-dd"), {
    value: "Original", options: ["Original", "22050 Hz", "32000 Hz", "44100 Hz", "48000 Hz"],
  });
  const slAudioBitrate = criarSlider($("#audio-bitrate-slider"), { min: 64, max: 320, value: 192, suffix: " kbps" });

  chamar("audio_formatos").then(lista => {
    if (lista && lista.length) ddAudioFormato.setOptions(lista, lista.includes("MP3") ? "MP3" : lista[0]);
  }).catch(() => {});

  ligarDropzone("#audio-dropzone", caminho => {
    estado.audio.caminho = caminho;
    $("#audio-dropzone-txt").textContent = caminho.split("/").pop() + "\n(arraste outro para trocar)";
    status("#audio-status", "Pronto.");
  });

  $("#audio-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.audio.pasta = p; $("#audio-pasta-txt").textContent = p; }
  });

  $("#audio-converter").addEventListener("click", async () => {
    if (!estado.audio.caminho) return toast("Escolha um áudio primeiro.");
    const btn = $("#audio-converter");
    btn.disabled = true;
    $("#audio-barra").style.width = "40%";
    status("#audio-status", "Convertendo…");
    try {
      const opcoes = {
        formato: ddAudioFormato.getValue(), bitrate: slAudioBitrate.getValue(),
        taxa: ddAudioTaxa.getValue(), pasta: estado.audio.pasta || null,
      };
      const r = await chamar("audio_converter", estado.audio.caminho, opcoes);
      $("#audio-barra").style.width = "100%";
      status("#audio-status", `Pronto! ${r.tamanho_fmt} — ${r.caminho.split("/").pop()}`, "ok");
    } catch (e) {
      status("#audio-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
      setTimeout(() => { $("#audio-barra").style.width = "0%"; }, 600);
    }
  });

  // ============================================================== YOUTUBE

  const ddYoutubeRes = criarDropdown($("#youtube-resolucao-dd"), { value: "", options: [] });

  $("#youtube-colar").addEventListener("click", async () => {
    try { $("#youtube-url").value = await navigator.clipboard.readText(); } catch (e) {}
  });

  $("#youtube-buscar").addEventListener("click", async () => {
    const url = $("#youtube-url").value.trim();
    if (!url) return;
    const btn = $("#youtube-buscar");
    btn.disabled = true;
    status("#youtube-status", "");
    try {
      const info = await chamar("youtube_info", url);
      estado.youtube.info = info;
      const opcoes = info.resolucoes.map(r => `${r.altura}p${r.tamanho_fmt && r.tamanho_fmt !== "—" ? " — " + r.tamanho_fmt : ""}`);
      ddYoutubeRes.setOptions(opcoes, opcoes[0] || "");
      $("#youtube-caixa").innerHTML = "";
      const wrap = el("div", { style: "text-align:left;color:#ffffff" });
      if (info.thumbnail) {
        const img = el("img", { src: info.thumbnail, style: "max-width:100%;border-radius:10px;margin-bottom:12px" });
        wrap.appendChild(img);
      }
      wrap.appendChild(el("div", { style: "font-weight:700;font-size:15px", text: info.titulo }));
      wrap.appendChild(el("div", { style: "font-size:13px;color:#aaaaaa;margin-top:4px", text: `${info.canal || ""} · ${info.duracao_fmt}` }));
      $("#youtube-caixa").style.display = "block";
      $("#youtube-caixa").appendChild(wrap);
    } catch (e) {
      status("#youtube-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  $("#youtube-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.youtube.pasta = p; $("#youtube-pasta-txt").textContent = p; }
  });

  window.tulao.on("youtube_progresso", d => { $("#youtube-barra").style.width = Math.max(2, d.pct) + "%"; });

  $("#youtube-baixar").addEventListener("click", async () => {
    if (!estado.youtube.info) return;
    const pasta = estado.youtube.pasta;
    if (!pasta) return toast("Escolha a pasta de destino.");
    const btn = $("#youtube-baixar");
    btn.disabled = true;
    status("#youtube-status", "Baixando…");
    try {
      const altura = parseInt(ddYoutubeRes.getValue()) || null;
      const r = await chamar("youtube_baixar", $("#youtube-url").value.trim(), pasta, altura, estado.youtube.info.titulo);
      status("#youtube-status", "Pronto! " + r.caminho.split("/").pop(), "ok");
    } catch (e) {
      status("#youtube-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
      setTimeout(() => { $("#youtube-barra").style.width = "0%"; }, 800);
    }
  });

  $("#youtube-atualizar-ferramenta").addEventListener("click", async () => {
    toast("Atualizando baixador…");
    try { await chamar("youtube_atualizar_ferramenta"); toast("Baixador atualizado. Feche e abra o app."); }
    catch (e) { toast("Erro: " + e.message); }
  });

  // =============================================================== SOCIAL

  $("#social-colar").addEventListener("click", async () => {
    try { $("#social-url").value = await navigator.clipboard.readText(); } catch (e) {}
  });

  $("#social-buscar").addEventListener("click", async () => {
    const url = $("#social-url").value.trim();
    if (!url) return;
    const btn = $("#social-buscar");
    btn.disabled = true;
    status("#social-status", "");
    try {
      const info = await chamar("social_info", url);
      estado.social.info = info;
      $("#social-caixa").innerHTML = "";
      const wrap = el("div", { style: "text-align:left;color:#ffffff;width:100%" });
      if (info.thumbnail) wrap.appendChild(el("img", { src: info.thumbnail, style: "max-width:100%;border-radius:10px;margin-bottom:12px" }));
      wrap.appendChild(el("div", { style: "font-weight:700;font-size:15px", text: info.titulo }));
      wrap.appendChild(el("div", { style: "font-size:13px;color:#aaaaaa;margin:4px 0 10px", text: `${info.plataforma} · ${info.autor || ""} · ${info.duracao_fmt}` }));
      info.itens.forEach((it, i) => {
        wrap.appendChild(el("div", { class: "chip-social", style: "background:#1c1c1e;border-color:#333" }, [
          el("div", { class: "ph", text: it.video ? "🎬" : "🖼" }),
          el("div", { text: `Item ${i + 1} — ${it.ext.toUpperCase()}` }),
        ]));
      });
      $("#social-caixa").style.display = "block";
      $("#social-caixa").appendChild(wrap);
    } catch (e) {
      status("#social-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  $("#social-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.social.pasta = p; $("#social-pasta-txt").textContent = p; }
  });

  window.tulao.on("social_progresso", d => { status("#social-status", `Baixando item ${d.item + 1} de ${d.total}…`); });

  $("#social-baixar").addEventListener("click", async () => {
    if (!estado.social.info) return;
    const pasta = estado.social.pasta;
    if (!pasta) return toast("Escolha a pasta de destino.");
    const btn = $("#social-baixar");
    btn.disabled = true;
    try {
      const r = await chamar("social_baixar", estado.social.info.itens, pasta, estado.social.info.titulo);
      status("#social-status", `Pronto! ${r.arquivos.length} arquivo(s) salvo(s).`, "ok");
    } catch (e) {
      status("#social-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  $("#social-atualizar-ferramenta").addEventListener("click", async () => {
    toast("Atualizando baixador…");
    try { await chamar("youtube_atualizar_ferramenta"); toast("Baixador atualizado. Feche e abra o app."); }
    catch (e) { toast("Erro: " + e.message); }
  });

  // ============================================================ PINTEREST

  $("#pinterest-colar").addEventListener("click", async () => {
    try { $("#pinterest-url").value = await navigator.clipboard.readText(); } catch (e) {}
  });

  $("#pinterest-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.pinterest.pasta = p; $("#pinterest-pasta-txt").textContent = p; }
  });

  $("#pinterest-buscar").addEventListener("click", async () => {
    const url = $("#pinterest-url").value.trim();
    if (!url) return;
    const btn = $("#pinterest-buscar");
    btn.disabled = true;
    status("#pinterest-status", "");
    try {
      const r = await chamar("pinterest_buscar", url);
      estado.pinterest.titulo = r.titulo;
      estado.pinterest.imagens = r.imagens;
      $("#pinterest-titulo").textContent = `${r.titulo} — ${r.imagens.length} imagem(ns)`;
      $("#pinterest-caixa").style.display = "none";
      const grid = $("#pinterest-grid");
      grid.innerHTML = "";
      r.imagens.slice(0, 60).forEach(src => grid.appendChild(el("img", { src })));
    } catch (e) {
      status("#pinterest-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  window.tulao.on("pinterest_progresso", d => {
    $("#pinterest-barra").style.width = (d.feito / d.total * 100) + "%";
    status("#pinterest-status", `Baixando ${d.feito} de ${d.total}…`);
  });

  $("#pinterest-baixar").addEventListener("click", async () => {
    if (!estado.pinterest.imagens.length) return;
    const pasta = estado.pinterest.pasta || await chamar("escolher_pasta");
    if (!pasta) return;
    estado.pinterest.pasta = pasta;
    $("#pinterest-pasta-txt").textContent = pasta;
    const btn = $("#pinterest-baixar");
    btn.disabled = true;
    try {
      const r = await chamar("pinterest_baixar_todas", estado.pinterest.titulo, estado.pinterest.imagens, pasta);
      status("#pinterest-status", `Pronto! ${r.baixadas} de ${r.total} imagens salvas em ${r.pasta}.`, "ok");
    } catch (e) {
      status("#pinterest-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
      setTimeout(() => { $("#pinterest-barra").style.width = "0%"; }, 800);
    }
  });

  // ============================================================= RENOMEAR

  const R = estado.renomear;
  const TIPOS_BLOCO = ["Nome atual", "Texto", "Separador", "Número", "Letra"];
  const ORDENS_NUMERO = ["Ordem de seleção", "Mais novos primeiro", "Mais antigos primeiro", "Ordem Alfabética"];

  $("#renomear-escolher-pasta").addEventListener("click", async () => {
    const pasta = await chamar("escolher_pasta");
    if (!pasta) return;
    R.pasta = pasta;
    $("#renomear-pasta-atual").textContent = pasta;
    const arquivos = await chamar("renomear_listar", pasta);
    R.arquivos = arquivos.map(a => ({ ...a, marcado: true }));
    renderListaArquivos();
    atualizarPreviaRenomear();
  });

  $("#renomear-escolher-arquivos").addEventListener("click", async () => {
    const caminhos = await chamar("escolher_arquivo", null, true);
    if (!caminhos || !caminhos.length) return;
    R.pasta = null;
    $("#renomear-pasta-atual").textContent = `${caminhos.length} arquivo(s) selecionado(s) manualmente`;
    R.arquivos = caminhos.map(c => ({ caminho: c, nome: c.split("/").pop(), marcado: true }));
    renderListaArquivos();
    atualizarPreviaRenomear();
  });

  $("#renomear-filtro").addEventListener("input", e => {
    R.filtro = e.target.value;
    renderListaArquivos();
  });

  function renderListaArquivos() {
    const cont = $("#renomear-lista");
    cont.innerHTML = "";
    const filtro = (R.filtro || "").trim().toLowerCase();
    const visiveis = filtro ? R.arquivos.filter(a => a.nome.toLowerCase().includes(filtro)) : R.arquivos;
    if (!R.arquivos.length) {
      cont.appendChild(el("div", { class: "vazio", text: "Nenhum arquivo carregado." }));
      return;
    }
    if (!visiveis.length) {
      cont.appendChild(el("div", { class: "vazio", text: "Nenhum arquivo corresponde ao filtro." }));
      return;
    }
    visiveis.forEach(a => {
      const c = el("input", { type: "checkbox" });
      c.checked = a.marcado;
      c.addEventListener("change", () => { a.marcado = c.checked; atualizarPreviaRenomear(); });
      cont.appendChild(el("div", { class: "item" }, [c, el("span", { class: "nome", text: a.nome })]));
    });
  }

  $all(".subaba-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $all(".subaba-btn").forEach(b => b.classList.remove("ativa"));
      btn.classList.add("ativa");
      R.subAba = btn.dataset.sub;
      ["montar", "retirar", "substituir"].forEach(s => {
        $("#renomear-sub-" + s).style.display = (s === R.subAba) ? "" : "none";
      });
      atualizarPreviaRenomear();
    });
  });

  async function atualizarPreviaRenomear() {
    const marcados = R.arquivos.filter(a => a.marcado).map(a => a.caminho);
    const corpo = $("#renomear-tabela-corpo");
    corpo.innerHTML = "";
    if (!marcados.length) return;
    try {
      const estadoEnvio = { arquivos: marcados, sub_aba: R.subAba, blocks: R.blocks, retirar: R.retirar, substituir: R.substituir, manter_ext: R.manterExt };
      const prev = await chamar("renomear_preview", estadoEnvio);
      R.ultimaPreview = prev;
      prev.forEach(p => {
        corpo.appendChild(el("tr", {}, [
          el("td", { text: p.original.split("/").pop() }),
          el("td", { text: p.novo }),
        ]));
      });
    } catch (e) { /* silencioso */ }
  }

  function renderConstrutor() {
    const cont = $("#renomear-construtor");
    cont.innerHTML = "";
    const addInserter = pos => {
      cont.appendChild(el("button", { type: "button", class: "inserter", text: "+", title: "Inserir bloco aqui", onclick: () => {
        R.blocks.splice(pos, 0, { tipo: "Texto", valor: "" });
        renderConstrutor(); atualizarPreviaRenomear();
      } }));
    };
    addInserter(0);
    R.blocks.forEach((b, i) => {
      cont.appendChild(renderChip(b, i));
      addInserter(i + 1);
    });
  }

  function renderChip(b, i) {
    const chip = el("div", { class: "chip", draggable: "true" });
    chip.dataset.index = i;
    chip.appendChild(el("div", { class: "alca" }, [0,1,2,3,4,5].map(() => el("span"))));

    const ddWrap = el("div", {});
    chip.appendChild(ddWrap);
    criarDropdown(ddWrap, {
      value: b.tipo, options: TIPOS_BLOCO, width: 130,
      onSelect: novoTipo => {
        const base = { tipo: novoTipo };
        if (novoTipo === "Separador") base.valor = "_";
        if (novoTipo === "Número") { base.inicio = 1; base.digitos = 3; base.ordem = "Ordem Alfabética"; }
        if (novoTipo === "Letra") { base.ordem = "Ordem Alfabética"; }
        R.blocks[i] = base;
        renderConstrutor(); atualizarPreviaRenomear();
      },
    });
    // o dd-btn dentro do chip fica mais compacto
    ddWrap.querySelector(".dd-btn").style.cssText = "height:32px;font-size:13px;padding:0 10px;border-radius:7px;min-width:110px";

    const extra = el("div", { class: "corpo-extra" });
    if (b.tipo === "Texto") {
      const inp = el("input", { type: "text", placeholder: "texto…" });
      inp.value = b.valor || "";
      inp.addEventListener("input", () => { b.valor = inp.value; atualizarPreviaRenomear(); });
      extra.appendChild(inp);
    } else if (b.tipo === "Separador") {
      const inp = el("input", { type: "text", placeholder: "_" });
      inp.style.width = "40px";
      inp.value = b.valor || "_";
      inp.addEventListener("input", () => { b.valor = inp.value; atualizarPreviaRenomear(); });
      extra.appendChild(inp);
    } else if (b.tipo === "Número") {
      const ini = el("input", { type: "number", title: "Início" });
      ini.value = b.inicio != null ? b.inicio : 1;
      ini.addEventListener("input", () => { b.inicio = parseInt(ini.value) || 0; atualizarPreviaRenomear(); });
      const dig = el("input", { type: "number", title: "Dígitos" });
      dig.value = b.digitos != null ? b.digitos : 3;
      dig.addEventListener("input", () => { b.digitos = parseInt(dig.value) || 1; atualizarPreviaRenomear(); });
      extra.appendChild(ini); extra.appendChild(dig);
    }
    if (b.tipo === "Número" || b.tipo === "Letra") {
      const ordemWrap = el("div", {});
      extra.appendChild(ordemWrap);
      criarDropdown(ordemWrap, {
        value: b.ordem || "Ordem Alfabética", options: ORDENS_NUMERO, width: 170,
        onSelect: novaOrdem => { b.ordem = novaOrdem; atualizarPreviaRenomear(); },
      });
      ordemWrap.querySelector(".dd-btn").style.cssText = "height:32px;font-size:12px;padding:0 8px;border-radius:7px;min-width:150px";
    }
    if (extra.children.length) chip.appendChild(extra);

    chip.appendChild(el("button", { type: "button", class: "remover", text: "×", title: "Remover bloco", onclick: () => {
      R.blocks.splice(i, 1);
      renderConstrutor(); atualizarPreviaRenomear();
    } }));

    chip.addEventListener("dragstart", e => { R.drag = i; chip.classList.add("arrastando"); e.dataTransfer.effectAllowed = "move"; });
    chip.addEventListener("dragend", () => { chip.classList.remove("arrastando"); R.drag = null; $all(".chip").forEach(c => c.classList.remove("sobre-alvo")); });
    chip.addEventListener("dragover", e => { e.preventDefault(); chip.classList.add("sobre-alvo"); });
    chip.addEventListener("dragleave", () => chip.classList.remove("sobre-alvo"));
    chip.addEventListener("drop", e => {
      e.preventDefault();
      if (R.drag === null || R.drag === i) return;
      const item = R.blocks.splice(R.drag, 1)[0];
      R.blocks.splice(i, 0, item);
      renderConstrutor(); atualizarPreviaRenomear();
    });

    return chip;
  }

  $("#retirar-buscar").addEventListener("input", e => { R.retirar.buscar = e.target.value; atualizarPreviaRenomear(); });
  $("#retirar-case").addEventListener("change", e => { R.retirar.case = e.target.checked; atualizarPreviaRenomear(); });
  $("#subst-buscar").addEventListener("input", e => { R.substituir.buscar = e.target.value; atualizarPreviaRenomear(); });
  $("#subst-trocar").addEventListener("input", e => { R.substituir.trocar = e.target.value; atualizarPreviaRenomear(); });
  $("#subst-case").addEventListener("change", e => { R.substituir.case = e.target.checked; atualizarPreviaRenomear(); });
  $("#renomear-manter-ext").addEventListener("change", e => { R.manterExt = e.target.checked; atualizarPreviaRenomear(); });

  $("#renomear-resetar").addEventListener("click", () => {
    R.blocks = [{ tipo: "Nome atual" }];
    R.retirar = { buscar: "", case: false };
    R.substituir = { buscar: "", trocar: "", case: false };
    $("#retirar-buscar").value = ""; $("#retirar-case").checked = false;
    $("#subst-buscar").value = ""; $("#subst-trocar").value = ""; $("#subst-case").checked = false;
    renderConstrutor();
    atualizarPreviaRenomear();
  });

  $("#renomear-aplicar").addEventListener("click", async () => {
    const marcados = R.arquivos.filter(a => a.marcado).map(a => a.caminho);
    if (!marcados.length) return toast("Selecione os arquivos na lista.");
    const btn = $("#renomear-aplicar");
    btn.disabled = true;
    try {
      const estadoEnvio = { arquivos: marcados, sub_aba: R.subAba, blocks: R.blocks, retirar: R.retirar, substituir: R.substituir, manter_ext: R.manterExt };
      const r = await chamar("renomear_aplicar", estadoEnvio);
      status("#renomear-status", `${r.renomeados} arquivo(s) renomeado(s).`, "ok");
      if (R.pasta) {
        const arquivos = await chamar("renomear_listar", R.pasta);
        R.arquivos = arquivos.map(a => ({ ...a, marcado: true }));
      } else if (R.ultimaPreview) {
        const mapa = {};
        R.ultimaPreview.forEach(p => { mapa[p.original] = p.novo; });
        R.arquivos = R.arquivos.map(a => {
          const novoNome = mapa[a.caminho];
          if (!novoNome || novoNome === a.nome) return a;
          const dir = a.caminho.slice(0, a.caminho.length - a.nome.length);
          return { ...a, caminho: dir + novoNome, nome: novoNome };
        });
      }
      renderListaArquivos();
      atualizarPreviaRenomear();
    } catch (e) {
      status("#renomear-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  // -------------------------------------------------- splitter (navegador)

  (function configurarSplitterRenomear() {
    const grid = $("#renomear-grid");
    const navegador = $("#renomear-navegador");
    const resizeV = $("#renomear-resize-v");
    const resizeH = $("#renomear-resize-h");
    if (!grid || !navegador || !resizeV || !resizeH) return;

    function arrastar(handle, aoMover) {
      handle.addEventListener("pointerdown", e => {
        e.preventDefault();
        handle.classList.add("arrastando");
        handle.setPointerCapture(e.pointerId);
        const mover = ev => aoMover(ev);
        const soltar = () => {
          handle.classList.remove("arrastando");
          window.removeEventListener("pointermove", mover);
          window.removeEventListener("pointerup", soltar);
        };
        window.addEventListener("pointermove", mover);
        window.addEventListener("pointerup", soltar);
      });
    }

    arrastar(resizeV, e => {
      const base = grid.getBoundingClientRect().left;
      let largura = e.clientX - base;
      largura = Math.max(180, Math.min(largura, grid.clientWidth - 260));
      grid.style.setProperty("--renomear-largura", largura + "px");
    });

    arrastar(resizeH, e => {
      const lista = $("#renomear-lista");
      const base = lista.getBoundingClientRect().top;
      let altura = e.clientY - base;
      altura = Math.max(90, Math.min(altura, window.innerHeight * 0.7));
      // Ao arrastar, a lista sai do tamanho flexível (acompanhando a altura
      // natural da coluna da direita) e passa a ter altura fixa, escolhida
      // pelo usuário.
      lista.style.flex = "0 0 auto";
      lista.style.height = altura + "px";
    });
  })();

  renderConstrutor();

  // =============================================================== FONTES

  $("#fontes-selecionar").addEventListener("click", async () => {
    const caminhos = await chamar("escolher_arquivo", null, true);
    if (!caminhos || !caminhos.length) return;
    for (const c of caminhos) {
      const info = await chamar("fontes_info", c);
      estado.fontes.arquivos.push({ caminho: c, nome: c.split("/").pop(), peso: info.peso, subfamilia: info.subfamilia });
      if (!$("#fontes-familia").value) $("#fontes-familia").value = info.familia;
    }
    renderTabelaFontes();
  });

  $("#fontes-limpar").addEventListener("click", () => {
    estado.fontes.arquivos = [];
    renderTabelaFontes();
    status("#fontes-status", "");
  });

  function renderTabelaFontes() {
    const corpo = $("#fontes-tabela-corpo");
    corpo.innerHTML = "";
    estado.fontes.arquivos.forEach(f => {
      corpo.appendChild(el("tr", {}, [
        el("td", { text: f.nome }),
        el("td", { text: f.peso }),
        el("td", { text: f.subfamilia }),
      ]));
    });
  }

  $("#fontes-escolher-pasta").addEventListener("click", async () => {
    const p = await chamar("escolher_pasta");
    if (p) { estado.fontes.pasta = p; $("#fontes-pasta-txt").textContent = "Saída: " + p; }
  });

  $("#fontes-corrigir").addEventListener("click", async () => {
    if (!estado.fontes.arquivos.length) return toast("Selecione ao menos uma fonte.");
    const familia = $("#fontes-familia").value.trim();
    if (!familia) return toast("Preencha a família.");
    const btn = $("#fontes-corrigir");
    btn.disabled = true;
    status("#fontes-status", "Corrigindo…");
    try {
      let ok = 0;
      for (const f of estado.fontes.arquivos) {
        const pasta = estado.fontes.pasta || f.caminho.split("/").slice(0, -1).join("/");
        await chamar("fontes_corrigir", f.caminho, pasta, familia, f.subfamilia, f.peso);
        ok++;
      }
      status("#fontes-status", `Pronto! ${ok} fonte(s) corrigida(s).`, "ok");
    } catch (e) {
      status("#fontes-status", "Erro: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  // ======================================================= Atualização

  let infoAtualizacao = null;
  window.tulao.on("atualizacao_disponivel", info => {
    infoAtualizacao = info;
    $("#update-texto").textContent = `A versão ${info.version} está disponível.`;
    $("#update-overlay").classList.add("aberto");
  });
  $("#update-depois").addEventListener("click", () => $("#update-overlay").classList.remove("aberto"));
  $("#update-agora").addEventListener("click", async () => {
    if (!infoAtualizacao) return;
    $("#update-agora").disabled = true;
    const r = await chamar("aplicar_atualizacao", infoAtualizacao);
    if (!r.ok) toast("Não foi possível iniciar o atualizador. Veja o log em ~/Library/Logs/UtilitarioDoTulao.log");
  });

  // ============================================================= cores

  const CORES_VAR = {
    branco: "--branco",
    vermelho: "--vermelho",
    roxo: "--roxo",
    cinza_claro: "--fundo",
    cinza_escuro: "--escuro",
  };
  let coresSalvas = null;

  // O "roxo claro" (fundo das abas ativas, da dropzone ao arrastar etc.)
  // não é escolhido à parte — segue sempre o mesmo tom do roxo principal,
  // só que com opacidade baixa, pra ficar sempre consistente com ele.
  function hexParaRgba(hex, alpha) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    if (!m) return null;
    const r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function aplicarCores(cores) {
    Object.entries(CORES_VAR).forEach(([chave, varCss]) => {
      if (cores[chave]) document.documentElement.style.setProperty(varCss, cores[chave]);
    });
    if (cores.roxo) {
      const claro = hexParaRgba(cores.roxo, 0.12);
      if (claro) document.documentElement.style.setProperty("--roxo-claro", claro);
    }
  }

  // Modo escuro: sobrescreve as mesmas variáveis de "cromo" da interface
  // (independente das cores customizadas) sempre que estiver ligado; ao
  // desligar, volta pras cores salvas do usuário.
  const PALETA_ESCURA = {
    "--fundo": "#1c1c1e",
    "--branco": "#2c2c2e",
    "--texto": "#f2f2f4",
    "--texto-secundario": "#a7a7ad",
    "--texto-apagado": "#76767c",
    "--borda": "#3a3a3d",
    "--borda-tracejada": "#48484b",
  };
  let modoEscuro = false;
  let modoEscuroSalvo = false;

  function aplicarTema(ligado) {
    const root = document.documentElement;
    if (ligado) {
      Object.entries(PALETA_ESCURA).forEach(([varCss, valor]) => root.style.setProperty(varCss, valor));
      root.setAttribute("data-tema", "escuro");
    } else {
      root.removeAttribute("data-tema");
      if (coresSalvas) aplicarCores(coresSalvas);
      else Object.keys(PALETA_ESCURA).forEach(varCss => root.style.removeProperty(varCss));
    }
  }

  function preencherFormularioCores(cores) {
    $all("#config-cores-lista .config-linha").forEach(linha => {
      const chave = linha.dataset.cor;
      const valor = cores[chave];
      if (!valor) return;
      linha.querySelector(".config-swatch").value = valor;
      linha.querySelector(".config-hex").textContent = valor;
    });
  }

  function lerFormularioCores() {
    const cores = {};
    $all("#config-cores-lista .config-linha").forEach(linha => {
      cores[linha.dataset.cor] = linha.querySelector(".config-swatch").value;
    });
    return cores;
  }

  $all("#config-cores-lista .config-linha").forEach(linha => {
    const input = linha.querySelector(".config-swatch");
    input.addEventListener("input", () => {
      linha.querySelector(".config-hex").textContent = input.value;
      aplicarCores({ [linha.dataset.cor]: input.value });
    });
  });

  $("#config-modo-escuro").addEventListener("change", e => {
    modoEscuro = e.target.checked;
    aplicarTema(modoEscuro);
  });

  $("#btn-config").addEventListener("click", async () => {
    try {
      const cores = coresSalvas || await chamar("obter_cores");
      preencherFormularioCores(cores);
    } catch (e) {}
    $("#config-modo-escuro").checked = modoEscuro;
    $("#config-overlay").classList.add("aberto");
  });

  $("#config-fechar").addEventListener("click", () => {
    if (coresSalvas) aplicarCores(coresSalvas); // descarta pré-visualização não salva
    modoEscuro = modoEscuroSalvo;
    $("#config-modo-escuro").checked = modoEscuro;
    aplicarTema(modoEscuro);
    $("#config-overlay").classList.remove("aberto");
  });

  $("#config-salvar").addEventListener("click", async () => {
    const cores = lerFormularioCores();
    try {
      const r = await chamar("salvar_cores", cores);
      coresSalvas = r.cores || cores;
      await chamar("definir_modo_escuro", modoEscuro);
      modoEscuroSalvo = modoEscuro;
      aplicarTema(modoEscuro);
      toast("Cores salvas.");
      $("#config-overlay").classList.remove("aberto");
    } catch (e) {
      toast("Não consegui salvar as cores: " + e.message);
    }
  });

  $("#config-restaurar").addEventListener("click", async () => {
    try {
      const r = await chamar("restaurar_cores_padrao");
      coresSalvas = r.cores;
      aplicarCores(coresSalvas);
      preencherFormularioCores(coresSalvas);
      toast("Cores restauradas ao padrão.");
    } catch (e) {
      toast("Não consegui restaurar as cores: " + e.message);
    }
  });

  $("#config-atualizar-tudo").addEventListener("click", async () => {
    const btn = $("#config-atualizar-tudo");
    btn.disabled = true;
    status("#config-atualizar-tudo-status", "Atualizando o baixador…");
    let avisoBaixador = "";
    try {
      await chamar("youtube_atualizar_ferramenta");
    } catch (e) {
      avisoBaixador = "Não consegui atualizar o baixador (yt-dlp). ";
    }
    status("#config-atualizar-tudo-status", "Procurando atualização do app…");
    try {
      const info = await chamar("checar_atualizacao");
      if (info && info.version) {
        infoAtualizacao = info;
        $("#update-texto").textContent = `A versão ${info.version} está disponível.`;
        $("#config-overlay").classList.remove("aberto");
        $("#update-overlay").classList.add("aberto");
      } else {
        status("#config-atualizar-tudo-status", avisoBaixador + "Baixador em dia. O app já está na versão mais recente.", avisoBaixador ? "erro" : "ok");
      }
    } catch (e) {
      status("#config-atualizar-tudo-status", avisoBaixador + "Não consegui checar a versão do app: " + e.message, "erro");
    } finally {
      btn.disabled = false;
    }
  });

  // =============================================================== CÓDIGO

  $all("#codigo-subabas .subaba-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $all("#codigo-subabas .subaba-btn").forEach(b => b.classList.remove("ativa"));
      btn.classList.add("ativa");
      $all(".codigo-ferramenta").forEach(c => c.classList.remove("ativa"));
      const alvo = $("#codigo-sub-" + btn.dataset.codigoSub);
      if (alvo) alvo.classList.add("ativa");
    });
  });

  function converterEmbedVimeoBehance(bruto) {
    const texto = (bruto || "").trim();
    if (!texto) return { ok: false };
    const mSrc = texto.match(/\ssrc=["']([^"']+)["']/i);
    const mId = mSrc && mSrc[1].match(/\/video\/(\d+)/);
    const mW = texto.match(/\swidth=["']?(\d+)["']?/i);
    const mH = texto.match(/\sheight=["']?(\d+)["']?/i);
    if (!mId || !mW || !mH) return { ok: false };
    const id = mId[1], largura = mW[1], altura = mH[1];
    const codigo = `<iframe src="https://player.vimeo.com/video/${id}?background=1" width="${largura}" height="${altura}" frameborder="0" allow="autoplay; fullscreen" allowfullscreen></iframe>`;
    return { ok: true, codigo, id, largura, altura };
  }

  const evbEntrada = $("#codigo-evb-entrada");
  const evbSaida = $("#codigo-evb-saida");
  const evbCopiar = $("#codigo-evb-copiar");

  evbEntrada.addEventListener("input", () => {
    const r = converterEmbedVimeoBehance(evbEntrada.value);
    if (!r.ok) {
      evbSaida.value = "";
      evbCopiar.disabled = true;
      status("#codigo-evb-status", evbEntrada.value.trim() ? "Não encontrei o link, a largura e a altura nesse código. Confira se colou o <iframe> completo." : "", evbEntrada.value.trim() ? "erro" : "");
      return;
    }
    evbSaida.value = r.codigo;
    evbCopiar.disabled = false;
    status("#codigo-evb-status", `Vídeo ${r.id} — ${r.largura}×${r.altura}.`, "ok");
  });

  evbCopiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(evbSaida.value);
    } catch (e) {
      evbSaida.removeAttribute("readonly");
      evbSaida.select();
      document.execCommand("copy");
      evbSaida.setAttribute("readonly", "readonly");
    }
    toast("Código copiado.");
  });

  // ============================================================= boot

  function iniciar() {
    chamar("versao_atual").then(v => { $("#versao-rodape").textContent = "v" + v; }).catch(() => {});
    chamar("obter_cores").then(cores => {
      coresSalvas = cores;
      aplicarCores(cores);
      return chamar("obter_modo_escuro");
    }).then(r => {
      modoEscuro = modoEscuroSalvo = !!(r && r.modo_escuro);
      aplicarTema(modoEscuro);
    }).catch(() => {});
    chamar("arquivos_iniciais").then(caminhos => {
      if (!caminhos || !caminhos.length) return;
      irParaAba("renomear");
      R.pasta = null;
      R.filtro = "";
      const filtroInput = $("#renomear-filtro");
      if (filtroInput) filtroInput.value = "";
      $("#renomear-pasta-atual").textContent = `${caminhos.length} arquivo(s) recebido(s) do Finder`;
      R.arquivos = caminhos.map(c => ({ caminho: c, nome: c.split("/").pop(), marcado: true }));
      renderListaArquivos();
      atualizarPreviaRenomear();
    }).catch(() => {});
  }
  if (window.pywebview) iniciar();
  else window.addEventListener("pywebviewready", iniciar);

})();
