/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  main: [
    {type: 'doc', id: 'README', label: 'Overview'},
    {type: 'doc', id: 'how-it-works', label: 'How it works'},
    {type: 'doc', id: 'setup', label: 'Get started'},
    {
      type: 'category',
      label: 'Using Anthill',
      link: {type: 'doc', id: 'USING_ANTHILL'},
      items: [
        {type: 'doc', id: 'USING_ANTHILL', label: 'Your workspace'},
        {type: 'doc', id: 'using-chat-and-knowledge', label: 'Chat & your knowledge base'},
        {type: 'doc', id: 'using-automation', label: 'Automating work'},
        {type: 'doc', id: 'using-models-and-compute', label: 'Models & compute'},
        {type: 'doc', id: 'using-connectors-and-personalization', label: 'Connectors & personalization'},
      ],
    },
  ],
};
module.exports = sidebars;
